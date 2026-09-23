from pathlib import Path

from clipper.install import TASK_NAME, register_script, unregister_script


def test_the_task_runs_the_app_at_sign_in_and_restarts_it():
    script = register_script(Path("C:/code/cs2-clipper"), Path("C:/code/cs2-clipper/.venv/Scripts/pythonw.exe"))
    assert "-Execute 'C:\\code\\cs2-clipper\\.venv\\Scripts\\pythonw.exe'" in script
    assert "-Argument '-m clipper run'" in script
    assert "-WorkingDirectory 'C:\\code\\cs2-clipper'" in script
    assert "New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME" in script
    assert "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)" in script
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in script
    assert "-LogonType Interactive -RunLevel Limited" in script
    assert f"-TaskName '{TASK_NAME}'" in script


def test_single_quotes_in_paths_are_doubled():
    script = register_script(Path("C:/it's here"), Path("C:/it's here/pythonw.exe"))
    assert "'C:\\it''s here'" in script


def test_uninstall_removes_the_same_task():
    assert unregister_script() == f"Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false"
