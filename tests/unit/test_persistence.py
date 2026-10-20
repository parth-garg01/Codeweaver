from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeweaver.persistence import (
    STORE_SCHEMA_VERSION,
    Checkpoint,
    CheckpointStore,
    StaleCheckpointError,
    default_store_dir,
)


def checkpoint(**kwargs) -> Checkpoint:
    defaults = {
        "project_root": "/src",
        "stage": "migrate",
        "file_hashes": {"a.py": "aaa", "b.py": "bbb"},
        "target": "typescript",
        "source_language": "python",
        "ir_schema_version": "1.1.0",
    }
    defaults.update(kwargs)
    return Checkpoint(**defaults)


class StoreLocationTests(unittest.TestCase):
    def test_default_is_beside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(default_store_dir(root), root.resolve() / ".codeweaver")

    def test_environment_override_is_honoured(self) -> None:
        import os

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CODEWEAVER_STORE_DIR"] = f"{tmp}/elsewhere"
            try:
                self.assertEqual(default_store_dir("/any"), Path(tmp) / "elsewhere")
            finally:
                del os.environ["CODEWEAVER_STORE_DIR"]

    def test_store_dir_is_in_the_default_ignore_list(self) -> None:
        from codeweaver.project_intake.ignore import DEFAULT_IGNORE_PATTERNS

        self.assertIn(".codeweaver/", DEFAULT_IGNORE_PATTERNS)


class CheckpointSerialisationTests(unittest.TestCase):
    def test_round_trips(self) -> None:
        original = checkpoint()
        self.assertEqual(Checkpoint.from_dict(original.to_dict()), original)

    def test_payload_records_the_store_schema_version(self) -> None:
        self.assertEqual(checkpoint().to_dict()["storeSchemaVersion"], STORE_SCHEMA_VERSION)

    def test_hashes_are_sorted_in_the_payload(self) -> None:
        payload = checkpoint(file_hashes={"z.py": "1", "a.py": "2"}).to_dict()
        self.assertEqual(list(payload["fileHashes"]), ["a.py", "z.py"])

    def test_an_unknown_store_schema_is_refused(self) -> None:
        payload = checkpoint().to_dict()
        payload["storeSchemaVersion"] = "99.0.0"
        with self.assertRaises(StaleCheckpointError) as ctx:
            Checkpoint.from_dict(payload)
        self.assertIn("re-run the analysis", str(ctx.exception))

    def test_a_payload_without_a_version_is_refused(self) -> None:
        with self.assertRaises(StaleCheckpointError):
            Checkpoint.from_dict({"projectRoot": "/src"})

    def test_no_source_code_is_stored(self) -> None:
        payload = json.dumps(checkpoint().to_dict())
        self.assertNotIn("def ", payload)
        self.assertNotIn("class ", payload)


class InvalidationTests(unittest.TestCase):
    def test_identical_inputs_are_current(self) -> None:
        saved = checkpoint()
        self.assertTrue(saved.is_current_for({"a.py": "aaa", "b.py": "bbb"}))
        self.assertEqual(saved.changed_files({"a.py": "aaa", "b.py": "bbb"}), {})

    def test_a_modified_file_is_detected(self) -> None:
        changed = checkpoint().changed_files({"a.py": "different", "b.py": "bbb"})
        self.assertEqual(changed, {"a.py": "modified"})

    def test_a_removed_file_is_detected(self) -> None:
        self.assertEqual(checkpoint().changed_files({"a.py": "aaa"}), {"b.py": "removed"})

    def test_an_added_file_is_detected(self) -> None:
        changed = checkpoint().changed_files({"a.py": "aaa", "b.py": "bbb", "c.py": "ccc"})
        self.assertEqual(changed, {"c.py": "added"})

    def test_several_changes_are_all_reported(self) -> None:
        changed = checkpoint().changed_files({"a.py": "x", "c.py": "ccc"})
        self.assertEqual(changed, {"a.py": "modified", "b.py": "removed", "c.py": "added"})

    def test_an_empty_project_invalidates_every_file(self) -> None:
        self.assertFalse(checkpoint().is_current_for({}))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = CheckpointStore.for_project(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_checkpoint_initially(self) -> None:
        self.assertFalse(self.store.exists())
        self.assertIsNone(self.store.load())

    def test_save_then_load_round_trips(self) -> None:
        self.store.save(checkpoint())
        loaded = self.store.load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.stage, "migrate")

    def test_save_stamps_a_creation_time(self) -> None:
        saved = checkpoint()
        self.store.save(saved)
        self.assertTrue(saved.created_at)

    def test_save_is_atomic_and_leaves_no_temporary_files(self) -> None:
        self.store.save(checkpoint())
        leftovers = [path.name for path in self.store.directory.iterdir() if path.suffix == ".tmp"]
        self.assertEqual(leftovers, [])

    def test_saving_twice_replaces_the_checkpoint(self) -> None:
        self.store.save(checkpoint(stage="analyze"))
        self.store.save(checkpoint(stage="validate"))
        self.assertEqual(self.store.load().stage, "validate")

    def test_an_unreadable_checkpoint_is_reported(self) -> None:
        self.store.directory.mkdir(parents=True, exist_ok=True)
        self.store.checkpoint_path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StaleCheckpointError):
            self.store.load()

    def test_a_non_object_checkpoint_is_reported(self) -> None:
        self.store.directory.mkdir(parents=True, exist_ok=True)
        self.store.checkpoint_path.write_text("[]", encoding="utf-8")
        with self.assertRaises(StaleCheckpointError):
            self.store.load()

    def test_report_round_trips(self) -> None:
        self.store.save_report({"status": "passed"})
        self.assertEqual(self.store.load_report(), {"status": "passed"})

    def test_missing_report_is_none(self) -> None:
        self.assertIsNone(self.store.load_report())

    def test_a_corrupt_report_is_treated_as_absent(self) -> None:
        self.store.directory.mkdir(parents=True, exist_ok=True)
        self.store.report_path.write_text("{bad", encoding="utf-8")
        self.assertIsNone(self.store.load_report())

    def test_clear_removes_what_was_written(self) -> None:
        self.store.save(checkpoint())
        self.store.save_report({"status": "passed"})
        removed = self.store.clear()
        self.assertEqual(len(removed), 2)
        self.assertFalse(self.store.exists())
        self.assertFalse(self.store.directory.exists())

    def test_clear_on_an_empty_store_is_harmless(self) -> None:
        self.assertEqual(self.store.clear(), [])

    def test_clear_keeps_an_unrelated_file_and_its_directory(self) -> None:
        self.store.save(checkpoint())
        stray = self.store.directory / "notes.txt"
        stray.write_text("mine", encoding="utf-8")
        self.store.clear()
        self.assertTrue(stray.is_file())
        self.assertTrue(self.store.directory.is_dir())


if __name__ == "__main__":
    unittest.main()
