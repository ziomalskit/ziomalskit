"""Exercise the documented heredoc with a real controlling terminal, CPU only."""
import errno
import os
from pathlib import Path
import pty
import select
import signal
import stat
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]


class PersonalRunbookAuthTests(unittest.TestCase):
    def test_auth_heredoc_reads_tty_without_echoing_password(self):
        document = (ROOT / 'docs/PERSONAL_FIRST_RUN.md').read_text()
        command = "python3 - <<'PY_AUTH'\n" + document.split(
            "python3 - <<'PY_AUTH'\n", 1)[1].split('\nPY_AUTH', 1)[0] + '\nPY_AUTH\n'
        user, password = 'test-user', 'synthetic-"password\\only'
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / 'curl.conf'
            pid, terminal = pty.fork()
            if pid == 0:
                try:
                    os.execve('/bin/bash', ['bash', '--noprofile', '--norc', '-c', command],
                              {**os.environ, 'AJ_CURL_CONFIG': str(config)})
                except BaseException:
                    os._exit(127)
            output, stage = bytearray(), 0
            prompts = [(b'Panel user: ', user), (b'Panel password: ', password)]
            deadline = time.monotonic() + 30
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    self.assertGreater(remaining, 0, 'auth heredoc did not finish')
                    self.assertTrue(select.select([terminal], [], [], remaining)[0],
                                    'auth heredoc did not finish')
                    try:
                        chunk = os.read(terminal, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break  # Linux PTY slave closed.
                        raise
                    if not chunk:
                        break
                    output.extend(chunk)
                    if stage < len(prompts) and prompts[stage][0] in output:
                        os.write(terminal, (prompts[stage][1] + '\n').encode())
                        stage += 1
                _, status = os.waitpid(pid, 0)
                pid = None
                self.assertEqual(os.waitstatus_to_exitcode(status), 0, output.decode())
                self.assertEqual(stage, 2)
                self.assertNotIn(password.encode(), output)
                escaped = (user + ':' + password).replace('\\', '\\\\').replace('"', '\\"')
                self.assertEqual(config.read_text(), 'user = "' + escaped + '"\n')
                self.assertEqual(stat.S_IMODE(config.stat().st_mode), 0o600)
            finally:
                os.close(terminal)
                if pid is not None:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)


if __name__ == '__main__':
    unittest.main()
