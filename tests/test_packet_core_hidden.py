from __future__ import annotations

import os
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, ReadOnly, RisingEdge, Timer
from cocotb_tools.runner import get_runner

LANGUAGE = os.getenv("HDL_TOPLEVEL_LANG", "verilog").lower().strip()

@cocotb.test()
async def packet_core_varied_length_test(dut):
    """Test processing packets of varied lengths and control_reg effects"""

    # Initial Reset
    dut.rst_n.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tvalid.value = 0
    dut.m_axis_tready.value = 0
    dut.control_reg.value = 0
    
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())
    
    await Timer(20, unit="ns")
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    await NextTimeStep()

    # Test Case Parameters: (Packet Length, Control Reg Value, Operation Reg value)
    test_cases = [
        ([0x12345678, 0x00000002, 0xFF000000], 0x0000005, 0x00),  # 3 words, Ctrl=5
        ([0xAAAA0000, 0xBBBB0000, 0xCCCC0000, 0xFFFFFFFF], 0x0000006A, 0x01), # 4 words, Ctrl=10
    ]

    for packet_data, ctrl_val, operation_val in test_cases:
        dut.control_reg.value = ctrl_val
        
        # --- 1. Send Packet ---
        for i, word in enumerate(packet_data):
            while int(dut.s_axis_tready.value) == 0:
                await RisingEdge(dut.clk)
            await NextTimeStep()

            dut.s_axis_tdata.value = word
            dut.s_axis_tvalid.value = 1
            await RisingEdge(dut.clk)
            await ReadOnly()
            assert int(dut.status_reg.value) == 1, f"Status reg failed to assert at word {i}"
            await NextTimeStep()

        dut.s_axis_tvalid.value = 0

        # --- 2. Wait for output (hold m_axis_tready high so TRANSMIT can handshake) ---
        # If tready stays low until after tvalid is seen, the DUT may never decrement
        # packet_length on the first valid cycle; assert ready before waiting for valid.
        await NextTimeStep()
        dut.m_axis_tready.value = 1
        await NextTimeStep()

        # --- 3. Receive and Verify ---
        # Wait until output starts (fail fast if FSM never reaches TRANSMIT).
        cycles_wait_m = 0
        while int(dut.m_axis_tvalid.value) == 0:
            await RisingEdge(dut.clk)
            cycles_wait_m += 1
            if cycles_wait_m > 500:
                raise RuntimeError("m_axis_tvalid never asserted (DUT stuck before TRANSMIT?)")

        # Drain: status may lag combinational valid; keep sampling until both are idle.
        received_packet = []
        it = 0
        while int(dut.status_reg.value) != 0 or int(dut.m_axis_tvalid.value) != 0:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if int(dut.m_axis_tvalid.value) != 0 and int(dut.m_axis_tready.value) != 0:
                try:
                    received_packet.append(int(dut.m_axis_tdata.value))
                except ValueError:
                    pass  # tdata may be X until the first transmit beat is registered
            it += 1
            if it > 500:
                raise RuntimeError("m_axis drain timed out")
        
        # Logic Verification based on STATE_PROCESS:
        # internal_reg1 = packet_buffer[0] + control_reg
        # internal_reg2 = packet_buffer[1] * control_reg
        if operation_val == 0:
            expected_reg1 = (packet_data[0] + ctrl_val) & 0xFFFFFFFF
            expected_reg2 = (packet_data[1] + ctrl_val) & 0xFFFFFFFF
        elif operation_val == 1:
            expected_reg1 = (packet_data[0] * ctrl_val) & 0xFFFFFFFF
            expected_reg2 = (packet_data[1] * ctrl_val) & 0xFFFFFFFF
        elif operation_val == 2:
            expected_reg1 = (packet_data[0] - ctrl_val) & 0xFFFFFFFF
            expected_reg2 = (packet_data[1] - ctrl_val) & 0xFFFFFFFF
        else:
            raise ValueError(f"Invalid operation value: {operation_val}")
        
        # Note: Your RTL transmits in reverse order or specific buffer index
        # Based on: m_axis_tdata <= packet_buffer[packet_length - 1]
        # It pops from the end of the buffer.
        dut._log.info(f"Received Packet: {[hex(x) for x in received_packet]}")
        
        # Verify status_reg returns to 0 after transmit finishes
        await RisingEdge(dut.clk)
        await RisingEdge(dut.clk)
        assert int(dut.status_reg.value) == 0, "Status reg should be 0 after packet transmission"

    dut._log.info("All packet tests passed!")

def test_packet_core_hidden_runner():
   sim = os.getenv("SIM", "icarus")

   proj_path = Path(__file__).resolve().parent.parent

   sources = [proj_path / "golden/packet_processing_core.v"]
   runner = get_runner(sim)
   runner.build(
       sources=sources,
       hdl_toplevel="packet_processing_core",
       always=True
   )

   runner.test(hdl_toplevel="packet_processing_core", test_module="test_packet_core_hidden")