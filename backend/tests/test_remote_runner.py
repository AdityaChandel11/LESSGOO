"""The guard on the deployed-database runner.

The seed truncates what it finds. This runner is the only thing standing
between "reseed the demo" and "destroy the local database that holds the
federation run", so its refusals are tested rather than trusted.
"""

import pytest

from scripts import remote


# IPv6 needs its brackets in a URL; the hostname parsed out of it is bare.
@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_a_local_target_is_refused(host, capsys):
    with pytest.raises(SystemExit) as exit_info:
        remote.describe("postgresql://u:p@{0}:5432/phc".format(host))
    assert "REFUSING" in str(exit_info.value)


def test_a_remote_target_is_described_and_allowed(capsys):
    host = remote.describe("postgresql://u:secret@dpg-x.singapore-postgres.render.com:5432/db")
    assert host == "dpg-x.singapore-postgres.render.com"


def test_the_password_is_never_printed(capsys):
    remote.describe("postgresql://someuser:hunter2@dpg-x.singapore-postgres.render.com/db")
    printed = capsys.readouterr().out
    assert "hunter2" not in printed and "someuser" not in printed
    assert "present, not shown" in printed


def test_an_empty_host_counts_as_local():
    # A DSN with no host connects over a local unix socket, which is exactly
    # the case this guard exists to stop.
    with pytest.raises(SystemExit):
        remote.describe("postgresql:///phc")
