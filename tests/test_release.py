"""发行ZIP的实际成员、校验和和覆盖保护。"""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('build_release', ROOT / 'scripts/build_release.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class ReleaseTests(unittest.TestCase):
    def test_root_alias_is_normalized_before_containment_check(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'nested').mkdir()
            (root / 'icon.png').write_bytes(b'icon')
            alias = root / 'nested' / '..'
            with patch.object(builder, 'ROOT', alias), patch.object(builder.subprocess, 'check_output', return_value=b'icon.png\0'):
                output = root / 'alias.zip'
                builder.build(output)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('PrintBridge/icon.png'), b'icon')

    def test_archive_is_deterministic_and_every_member_has_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'main.py').write_bytes(b'print("test")\n')
            (root / 'icon.png').write_bytes(b'\x89PNG\r\n\x1a\n')
            names = b'main.py\0icon.png\0'
            with patch.object(builder, 'ROOT', root), patch.object(builder.subprocess, 'check_output', return_value=names):
                a, b = root / 'a.zip', root / 'b.zip'
                self.assertEqual(builder.build(a), builder.build(b))
                self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as archive:
                self.assertIsNone(archive.testzip())
                sums = archive.read('PrintBridge/SHA256SUMS').decode().splitlines()
                self.assertEqual(len(sums), 2)
                for line in sums:
                    digest, name = line.split('  ', 1)
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)

    def test_output_cannot_truncate_tracked_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'main.py'
            source.write_bytes(b'keep')
            with patch.object(builder, 'ROOT', root), patch.object(builder.subprocess, 'check_output', return_value=b'main.py\0'):
                with self.assertRaises(ValueError):
                    builder.build(source)
            self.assertEqual(source.read_bytes(), b'keep')

    def test_tracked_private_runtime_files_are_rejected(self):
        for name in ('config.json', 'print_history.db', '.env', 'logs/chat.txt', '../outside.py'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / 'out.zip'
                with patch.object(builder, 'ROOT', root), patch.object(builder.subprocess, 'check_output', return_value=(name + '\0').encode()):
                    with self.assertRaises(ValueError):
                        builder.build(output)
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
