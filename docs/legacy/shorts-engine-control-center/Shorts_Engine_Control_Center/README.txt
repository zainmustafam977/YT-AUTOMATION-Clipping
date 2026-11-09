================================================================
       ⚡ SHORTS ENGINE - 1-CLICK CONTROL CENTER ⚡
================================================================

This directory contains one-click clickable scripts (.bat) to run
and manage every task in the YouTube Shorts Automation Engine.

QUICK START:
  Double-click "START_HERE.bat" to open the master interactive menu.

----------------------------------------------------------------
FOLDER STRUCTURE & INDIVIDUAL SCRIPTS:
----------------------------------------------------------------

[START_HERE.bat]
  - Master interactive menu (select 1-9 to trigger any task).

[01_Servers_and_Workers\]
  - start_dashboard.bat  : Starts the Web Dashboard on port 8000
                           and automatically opens your browser.
  - start_worker.bat     : Starts the background worker queue
                           for rendering and quality gate jobs.

[02_Testing_and_Validation\]
  - run_all_tests.bat       : Runs all 89 automated tests (100% pass).
  - run_dashboard_tests.bat : Runs the 9 dashboard enhancement tests.

[03_Operations_and_Pipelines\]
  - process_video.bat : Prompts for a YouTube URL/ID to ingest and process.
  - add_channel.bat   : Prompts for Channel ID & Name to register.

[04_Admin_and_Distribution\]
  - create_user.bat   : Prompts to create Admin, Operator, or Viewer users.
  - build_bundle.bat  : Rebuilds shorts_automation_m3_bundle.zip + SHA256.

----------------------------------------------------------------
NOTES:
  - All scripts automatically resolve your Python 3.11 environment
    (%LOCALAPPDATA%\Programs\Python\Python311\python.exe).
  - All scripts pause upon completion so you can read the output.
================================================================
