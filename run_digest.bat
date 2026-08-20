@echo off
rem One digest pass - schedule via Windows Task Scheduler at 11:00, 13:30 and
rem 15:20 IST Mon-Fri. Three separate task triggers beat one long-lived
rem process: a crashed 11:00 run cannot cost you the 13:30 one.
rem The explicit "exit /b %errorlevel%" matters - without it a python failure
rem can still report success up the chain to Task Scheduler.
cd /d d:\PythonProjects\anticipatory_breakout
python main.py scan >> logs\scheduler.log 2>&1
exit /b %errorlevel%
