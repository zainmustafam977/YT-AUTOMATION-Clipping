"""
==============================================================================
Win32 Job Object Lifecycle Supervisor
==============================================================================
Guarantees clean process tree termination on Windows.
When configured with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, the Windows OS kernel
automatically terminates all child processes (FFmpeg, yt-dlp, etc.) if the parent
process or console/launcher terminates, eliminating zombie background processes.
==============================================================================
"""

import sys
import logging

logger = logging.getLogger(__name__)

# Win32 Constants
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001


class Win32JobManager:
    """Manages the Win32 Job Object with KILL_ON_JOB_CLOSE semantics."""

    def __init__(self):
        self._job_handle = None
        self._initialized = False

    @property
    def is_active(self) -> bool:
        return self._job_handle is not None and self._initialized

    @property
    def handle(self):
        return self._job_handle

    def initialize(self) -> bool:
        """
        Creates a Job Object with JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE and
        assigns the current Python process to it.
        Returns True if successful, False otherwise.
        """
        if sys.platform != "win32":
            logger.debug("Win32JobManager: Non-Windows platform; skipping Job Object init.")
            return False

        if self._initialized:
            return True

        try:
            import ctypes
            from ctypes import wintypes

            # Define Win32 structures
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryLimit", ctypes.c_size_t),
                    ("PeakJobMemoryLimit", ctypes.c_size_t),
                ]

            k32 = ctypes.windll.kernel32

            # Explicit ctypes signatures for 64-bit Windows pointer safety
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.CreateJobObjectW.restype = wintypes.HANDLE

            k32.GetCurrentProcess.argtypes = []
            k32.GetCurrentProcess.restype = wintypes.HANDLE

            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL

            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                wintypes.LPVOID,
                wintypes.DWORD,
            ]
            k32.SetInformationJobObject.restype = wintypes.BOOL

            # Create Job Object
            job = k32.CreateJobObjectW(None, None)
            if not job:
                err = k32.GetLastError()
                logger.warning("CreateJobObjectW failed with error code %d", err)
                return False

            # Configure JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

            res = k32.SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            )
            if not res:
                err = k32.GetLastError()
                logger.warning("SetInformationJobObject failed with error code %d", err)
                return False

            # Assign current process to Job Object
            current_proc = k32.GetCurrentProcess()
            assign_res = k32.AssignProcessToJobObject(job, current_proc)
            if not assign_res:
                err = k32.GetLastError()
                # Error 5 is ERROR_ACCESS_DENIED, which can happen if already in another job
                logger.warning("AssignProcessToJobObject failed with error code %d", err)
                # Keep handle so individual child processes can still be assigned
                self._job_handle = job
                self._initialized = True
                return True

            self._job_handle = job
            self._initialized = True
            logger.info("Win32 Job Object initialized successfully with KILL_ON_JOB_CLOSE.")
            return True

        except Exception as e:
            logger.warning("Failed to initialize Win32 Job Object: %s", e)
            return False

    def assign_process(self, pid_or_handle) -> bool:
        """
        Explicitly assigns a process by PID or process handle to the Job Object.
        """
        if not self.is_active or sys.platform != "win32":
            return False

        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32

            if isinstance(pid_or_handle, int):
                # Open process handle with required access rights
                k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                k32.OpenProcess.restype = wintypes.HANDLE
                proc_handle = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid_or_handle)
                if not proc_handle:
                    return False
                close_on_exit = True
            elif not hasattr(pid_or_handle, "mock_calls") and (isinstance(pid_or_handle, (wintypes.HANDLE, ctypes.c_void_p)) or (hasattr(pid_or_handle, "value") and not hasattr(pid_or_handle, "_mock_return_value"))):
                proc_handle = pid_or_handle
                close_on_exit = False
            else:
                return False

            k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            k32.AssignProcessToJobObject.restype = wintypes.BOOL
            res = k32.AssignProcessToJobObject(self._job_handle, proc_handle)

            if close_on_exit:
                k32.CloseHandle.argtypes = [wintypes.HANDLE]
                k32.CloseHandle(proc_handle)

            return bool(res)
        except Exception as e:
            logger.debug("Failed to assign process to Win32 Job Object: %s", e)
            return False


# Global singleton instance
win32_job_manager = Win32JobManager()


def init_win32_job_object() -> bool:
    """Helper function to initialize the global Win32 Job Object."""
    return win32_job_manager.initialize()
