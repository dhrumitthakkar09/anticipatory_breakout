' Launches one digest pass with NO console window - three visible windows a
' day during market hours invites an accidental close, and there is nothing to
' watch interactively anyway. All output still goes to logs\scheduler.log and
' logs\scanner.log.
'
' Run(..., 0, True) WAITS and WScript.Quit propagates the exit code. The
' fire-and-forget form (False) makes wscript.exe itself "the task" as far as
' Task Scheduler is concerned, so it reports success no matter what happened
' inside - exactly how 4/5 bots showed green on 2026-08-06 despite a login
' crash. Hidden (windowStyle=0) is independent of waiting.
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "d:\PythonProjects\anticipatory_breakout"
exitCode = sh.Run("""d:\PythonProjects\anticipatory_breakout\run_digest.bat""", 0, True)
WScript.Quit(exitCode)
