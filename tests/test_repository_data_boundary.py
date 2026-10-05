from __future__ import annotations

import subprocess

import pytest

from tools import check_tracked_user_data


def test_repository_check_rejects_private_files_outside_user_data(monkeypatch, capsys):
    blocked = [
        'user_data/databases/grading_system.db', 'question_bank/QUESTION_BANK.DB',
        'config/api_profiles.json.bak', 'config/.env.production', 'keys/client.pem',
        'question_bank/question_bank.db-wal', 'temporary/export.sqlite3.bak',
        'backups/private.zip', 'output/TEST-report.html', 'runtime/python/python.exe',
        'frontend/dist/index.html', 'scratch/private\nexam.json',
    ]
    paths = [
        *blocked, 'backend/repositories/db_manager.py', 'docs/security/SECURITY.md',
        'config/.env.example', 'frontend/src/api/sessions.ts',
    ]

    def git_listing(command, **kwargs):
        assert command == ['git', 'ls-files', '-z']
        return subprocess.CompletedProcess(command, 0, stdout='\0'.join(paths) + '\0')

    monkeypatch.setattr(check_tracked_user_data.subprocess, 'run', git_listing)
    assert check_tracked_user_data.main() == 1
    output = capsys.readouterr().out
    assert all(repr(path) in output for path in blocked)
    assert 'backend/repositories/db_manager.py' not in output


def test_repository_check_accepts_source_and_reports_git_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        check_tracked_user_data.subprocess, 'run',
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout='backend/scan_grading/scanner.py\0config/.env.template\0',
        ),
    )
    assert check_tracked_user_data.main() == 0
    assert 'OK:' in capsys.readouterr().out

    def git_failure(command, **kwargs):
        raise subprocess.CalledProcessError(128, command)

    monkeypatch.setattr(check_tracked_user_data.subprocess, 'run', git_failure)
    with pytest.raises(subprocess.CalledProcessError):
        check_tracked_user_data.main()
