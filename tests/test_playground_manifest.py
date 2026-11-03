"""Checks for the generated iximiuz playground manifest."""

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "playground/iximiuz/render_manifest.py"
SPEC = importlib.util.spec_from_file_location("render_manifest", SCRIPT)
render_manifest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(render_manifest)


class PlaygroundManifestTests(unittest.TestCase):
    def setUp(self):
        self.manifest = render_manifest.render()
        self.playground = self.manifest["playground"]

    def test_checked_in_manifest_is_current(self):
        checked_in = (ROOT / "playground/iximiuz/manifest.yaml").read_text()
        self.assertEqual(
            checked_in,
            render_manifest.manifest_text(),
            "Run: python3 playground/iximiuz/render_manifest.py (then publish.sh)",
        )

    def test_lab_files_are_embedded_verbatim(self):
        files = {entry["path"]: entry for entry in self.playground["startupFiles"]}
        self.assertLessEqual(len(files), 20, "iximiuz allows at most 20 startup files")
        for path in render_manifest.LAB_FILES:
            entry = files[f"{render_manifest.LAB_DIR}/{path}"]
            self.assertEqual(entry["content"], (ROOT / path).read_text(), path)

    def test_init_tasks_form_a_dag_on_known_machines(self):
        tasks = self.playground["initTasks"]
        machines = {machine["name"] for machine in self.playground["machines"]}
        for name, task in tasks.items():
            self.assertIn(task["machine"], machines, name)
            for dependency in task.get("needs", []):
                self.assertIn(dependency, tasks, f"{name} needs unknown task {dependency}")

        visiting, done = set(), set()

        def visit(name):
            self.assertNotIn(name, visiting, f"dependency cycle through {name}")
            if name in done:
                return
            visiting.add(name)
            for dependency in tasks[name].get("needs", []):
                visit(dependency)
            visiting.remove(name)
            done.add(name)

        for name in tasks:
            visit(name)

    def test_lab_make_targets_exist(self):
        makefile = (ROOT / "Makefile").read_text()
        for name, task in self.playground["initTasks"].items():
            for line in task["run"].splitlines():
                if line.startswith("make "):
                    target = line.split()[1]
                    self.assertIn(f"\n{target}:", makefile, f"{name} runs missing target {target}")

    def test_gitlab_tab_targets_the_nodeport(self):
        tab = next(tab for tab in self.playground["tabs"] if tab["id"] == "gitlab")
        self.assertEqual(tab["number"], render_manifest.WEB_NODE_PORT)
        self.assertLessEqual(len(self.playground["tabs"]), 10)

    def test_yaml_round_trip(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        self.assertEqual(yaml.safe_load(render_manifest.manifest_text()), self.manifest)


if __name__ == "__main__":
    unittest.main()
