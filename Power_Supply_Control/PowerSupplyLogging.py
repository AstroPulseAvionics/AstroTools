import asyncio
import csv
import time
from datetime import datetime
from pathlib import Path

import pyvisa
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

RESOURCE = "USB0::0x0957::0x0807::N5772A-US13D4859K::0::INSTR"


def make_log_file_path():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(__file__).resolve().parent / f"600V_power_supply_log_{timestamp}.csv"


async def visa_query(psu, visa_lock, cmd):
    async with visa_lock:
        return await asyncio.to_thread(psu.query, cmd)


async def visa_write(psu, visa_lock, cmd):
    async with visa_lock:
        await asyncio.to_thread(psu.write, cmd)


async def logging_loop(psu, visa_lock, state, stop_event):
    with open(state["log_file"], "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "voltage_V", "current_A"])

        while not stop_event.is_set():
            timestamp = time.time()
            try:
                voltage = float((await visa_query(psu, visa_lock, "MEAS:VOLT?")).strip())
                current = float((await visa_query(psu, visa_lock, "MEAS:CURR?")).strip())
            except Exception as exc:
                print(f"Measurement error: {exc}")
                continue

            writer.writerow([timestamp, voltage, current])
            f.flush()

# Commands
# status                Query and print PSU status
# setv <volts>          Set voltage setpoint
# setc <amps>           Set current setpoint
# out on|off            Turn output on/off
# raw <SCPI command>    Send a raw SCPI write command
# quit                  Stop logging and exit

async def command_loop(psu, visa_lock, state, stop_event):
    session = PromptSession("> ")

    while not stop_event.is_set():
        try:
            line = await session.prompt_async()
        except (EOFError, KeyboardInterrupt):
            stop_event.set()
            break

        cmd = line.strip()
        if not cmd:
            continue

        parts = cmd.split()
        action = parts[0].lower()

        try:
            if action in {"quit", "exit"}:
                await visa_write(psu, visa_lock, "OUTP OFF")
                print("Output -> OFF")
                stop_event.set()
            elif action == "status":
                idn = (await visa_query(psu, visa_lock, "*IDN?")).strip()
                voltage = (await visa_query(psu, visa_lock, "MEAS:VOLT?")).strip()
                current = (await visa_query(psu, visa_lock, "MEAS:CURR?")).strip()
                outp = (await visa_query(psu, visa_lock, "OUTP?")).strip()
                print(f"IDN={idn}")
                print(f"V={voltage} V, I={current} A, OUTP={outp}")
            elif action == "setv" and len(parts) == 2:
                value = float(parts[1])
                await visa_write(psu, visa_lock, f"VOLT {value}")
                print(f"Voltage setpoint -> {value} V")
            elif action == "setc" and len(parts) == 2:
                value = float(parts[1])
                await visa_write(psu, visa_lock, f"CURR {value}")
                print(f"Current setpoint -> {value} A")
            elif action == "out" and len(parts) == 2:
                state_word = parts[1].lower()
                if state_word not in {"on", "off"}:
                    print("Usage: out on|off")
                    continue
                await visa_write(psu, visa_lock, f"OUTP {state_word.upper()}")
                print(f"Output -> {state_word.upper()}")
            elif action == "raw" and len(parts) >= 2:
                raw_cmd = cmd[len(parts[0]) + 1 :]
                await visa_write(psu, visa_lock, raw_cmd)
                print(f"Sent: {raw_cmd}")
            else:
                print("Unknown command.")
        except Exception as exc:
            print(f"Command error: {exc}")


async def main():
    state = {"log_file": make_log_file_path()}
    stop_event = asyncio.Event()
    visa_lock = asyncio.Lock()

    rm = pyvisa.ResourceManager()
    psu = rm.open_resource(RESOURCE)
    psu.write_termination = "\n"
    psu.read_termination = "\n"
    psu.timeout = 5000  # ms

    try:
        idn = (await visa_query(psu, visa_lock, "*IDN?")).strip()
        print(f"Connected to {idn}")
        await visa_write(psu, visa_lock, "VOLT 0")
        await visa_write(psu, visa_lock, "CURR 0")
        await visa_write(psu, visa_lock, "OUTP OFF")
        print("Default setup applied: VOLT 0, CURR 0, OUTP OFF")
        print(f"Logging to {state['log_file']}")

        log_task = asyncio.create_task(logging_loop(psu, visa_lock, state, stop_event))
        try:
            with patch_stdout():
                await command_loop(psu, visa_lock, state, stop_event)
        finally:
            stop_event.set()
            await log_task
    finally:
        try:
            err = (await visa_query(psu, visa_lock, "SYST:ERR?")).strip()
            print(f"SYST:ERR? -> {err}")
        except Exception:
            pass
        psu.close()
        rm.close()


if __name__ == "__main__":
    asyncio.run(main())
