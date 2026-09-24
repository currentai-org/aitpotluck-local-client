"""aipotluck.service.newt_supervisor -- the newt (Pangolin tunnel) process supervisor.

The one thing this file exists to pin: tunnel_connected() correctly distinguishes "the newt
process is alive" from "the tunnel is actually connected" by reading newt's own log output --
process liveness alone cannot make this distinction, since a real failure this project hit shows
newt "running" for 20+ hours while its own internal retry loop never once succeeds. Real log
files are used throughout (not a mocked reader) -- this is exactly the kind of thing a mock could
make look correct while testing nothing.
"""

from __future__ import annotations

from pathlib import Path

from aipotluck.service.newt_supervisor import (
    NewtSupervisor,
    _classify_tunnel_state,
    _tail_log_lines,
)


class TestClassifyTunnelState:
    def test_no_lines_is_unknown(self):
        assert _classify_tunnel_state([]) is None

    def test_only_startup_noise_is_unknown(self):
        lines = ["INFO: 2026/09/24 Starting newt version 1.17.0", "INFO: 2026/09/24 Connecting to endpoint..."]
        assert _classify_tunnel_state(lines) is None

    def test_success_line_is_connected(self):
        lines = ["INFO: 2026/09/24 Connecting...", "INFO: 2026/09/24 Tunnel connection to server established successfully!"]
        assert _classify_tunnel_state(lines) is True

    def test_error_line_is_not_connected(self):
        lines = [
            "ERROR: 2026/09/24 14:12:43 Failed to connect: failed to get token: "
            "dial tcp 127.0.0.1:80: connect: connection refused. Retrying in 3s...",
        ]
        assert _classify_tunnel_state(lines) is False

    def test_most_recent_signal_wins_error_after_success(self):
        # A real tunnel that later drops must not keep reporting "connected" forever.
        lines = [
            "INFO: Tunnel connection to server established successfully!",
            "ERROR: 2026/09/24 15:00:00 connection lost, retrying...",
        ]
        assert _classify_tunnel_state(lines) is False

    def test_most_recent_signal_wins_success_after_error(self):
        # The real, common case: newt retries a few times, then succeeds -- must not get stuck
        # reporting the earlier failures forever.
        lines = [
            "ERROR: 2026/09/24 14:12:43 Failed to connect: connection refused. Retrying in 3s...",
            "ERROR: 2026/09/24 14:12:46 Failed to connect: connection refused. Retrying in 3s...",
            "INFO: 2026/09/24 14:12:50 Tunnel connection to server established successfully!",
        ]
        assert _classify_tunnel_state(lines) is True


class TestTailLogLines:
    def test_missing_file_is_empty_not_an_error(self, tmp_path):
        assert _tail_log_lines(tmp_path / "nope.log") == []

    def test_reads_real_file_contents(self, tmp_path):
        path = tmp_path / "newt.log"
        path.write_text("line one\nline two\n", encoding="utf-8")
        assert _tail_log_lines(path) == ["line one", "line two"]

    def test_bounded_by_max_bytes_reads_only_the_tail(self, tmp_path):
        path = tmp_path / "newt.log"
        # Many short lines -- with a small max_bytes, only the last few should come back.
        path.write_text("".join(f"line-{i}\n" for i in range(1000)), encoding="utf-8")
        lines = _tail_log_lines(path, max_bytes=100)
        assert lines[-1] == "line-999"
        assert len(lines) < 20


class TestNewtSupervisorTunnelConnected:
    def test_no_log_dir_is_unknown(self):
        supervisor = NewtSupervisor(
            Path("/bin/true"), tunnel_id="i", tunnel_secret="s", tunnel_endpoint="e", log_dir=None,
        )
        assert supervisor.tunnel_connected() is None

    def test_reads_the_real_configured_log_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "newt.log").write_text(
            "ERROR: 2026/09/24 14:12:43 Failed to connect: connection refused. Retrying in 3s...\n",
            encoding="utf-8",
        )
        supervisor = NewtSupervisor(
            Path("/bin/true"), tunnel_id="i", tunnel_secret="s", tunnel_endpoint="e", log_dir=log_dir,
        )
        assert supervisor.tunnel_connected() is False

    def test_info_includes_tunnel_connected_without_affecting_running(self, tmp_path):
        # The exact discrepancy this feature exists to surface: running=True (set directly on
        # the internal info, as _spawn_process would after a real start) alongside a log that
        # shows the tunnel was never actually established.
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "newt.log").write_text(
            "ERROR: 2026/09/24 14:12:43 Failed to connect: connection refused. Retrying in 3s...\n",
            encoding="utf-8",
        )
        supervisor = NewtSupervisor(
            Path("/bin/true"), tunnel_id="i", tunnel_secret="s", tunnel_endpoint="e", log_dir=log_dir,
        )
        supervisor._info.running = True
        supervisor._info.pid = 12345

        info = supervisor.info()

        assert info.running is True
        assert info.pid == 12345
        assert info.tunnel_connected is False

    def test_info_reflects_a_real_successful_connection(self, tmp_path):
        log_dir = tmp_path / "logs"
        log_dir.mkdir()
        (log_dir / "newt.log").write_text(
            "INFO: 2026/09/24 14:12:50 Tunnel connection to server established successfully!\n",
            encoding="utf-8",
        )
        supervisor = NewtSupervisor(
            Path("/bin/true"), tunnel_id="i", tunnel_secret="s", tunnel_endpoint="e", log_dir=log_dir,
        )
        supervisor._info.running = True

        assert supervisor.info().tunnel_connected is True
