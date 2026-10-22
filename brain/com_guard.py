"""Leaving a thread's COM session without crashing.

The audio checks (pycaw, comtypes) call CoInitialize, use COM objects held in local variables, and used to call CoUninitialize in a
`finally`. Those objects are only released afterwards (when the function returns or the garbage collector runs), and releasing a COM
interface after its apartment was torn down is an access violation: logs/backend.log showed 58 of them, "OSError: exception: access
violation writing 0x0", and one backend exit with 0xc0000005. So the session is left initialised (it belongs to the thread and costs
nothing) and the collector is run, which releases whatever is already unreachable while COM is still alive.
"""
import gc


def end() -> None:
    gc.collect()
