from __future__ import annotations
import os
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, RisingEdge, Timer, ReadOnly
from cocotb_tools.runner import get_runner

# Constants
ADDITION = 0
MULTIPLICATION = 1
SUBTRACTION = 2

async def start_clock(dut):
    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

async def reset_dut(dut):
    dut.rst_n.value = 0
    dut.control_reg.value = 0
    dut.operation.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tvalid.value = 0
    dut.m_axis_tready.value = 0
    await Timer(20, unit="ns")
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

async def send_packet(dut, data_list):
    for word in data_list:
        while int(dut.s_axis_tready.value) == 0:
            await RisingEdge(dut.clk)
        dut.s_axis_tdata.value = word
        dut.s_axis_tvalid.value = 1
        await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0

async def receive_packet(dut):
    await NextTimeStep()
    received = []
    dut.m_axis_tready.value = 1
    await RisingEdge(dut.clk)
    while int(dut.m_axis_tvalid.value) == 0:
        await RisingEdge(dut.clk)
    while int(dut.m_axis_tvalid.value) != 0:
        await ReadOnly()
        received.append(int(dut.m_axis_tdata.value))
        await RisingEdge(dut.clk)
    dut.m_axis_tready.value = 0
    return received

@cocotb.test()
async def basic_addition_test(dut):
    """Test Case 1: Basic Addition with short packet"""
    await start_clock(dut)
    await reset_dut(dut)
    dut.control_reg.value = 10
    dut.operation.value = ADDITION
    
    # [Word0, Word1, EOP]
    payload = [100, 200, 0xFF000000]
    await send_packet(dut, payload)
    results = await receive_packet(dut)
    
    # Reverse LIFO: [EOP, Word1+10, Word0+10, Footer]
    assert results[1] == 210, f"Expected 210, got {results[1]}"
    assert results[2] == 110, f"Expected 110, got {results[2]}"
    dut._log.info("Basic Addition Test Passed")

@cocotb.test()
async def medium_multiplication_test(dut):
    """Test Case 2: Multiplication with medium packet"""
    await start_clock(dut)
    await reset_dut(dut)
    dut.control_reg.value = 5
    dut.operation.value = MULTIPLICATION
    
    # [Word0, Word1, Word2, EOP]
    payload = [10, 20, 30, 0xFF123456]
    await send_packet(dut, payload)
    results = await receive_packet(dut)
    
    # result indices (Reverse): 0:EOP, 1:Word2, 2:Word1*5, 3:Word0*5, 4:Footer
    assert results[2] == 100, f"Expected 100, got {results[2]}"
    assert results[3] == 50, f"Expected 50, got {results[3]}"
    dut._log.info("Medium Multiplication Test Passed")

@cocotb.test()
async def comprehensive_subtraction_status_test(dut):
    """Test Case 3: Subtraction, Status Reg, and Length 255 check"""
    await start_clock(dut)
    await reset_dut(dut)
    dut.control_reg.value = 50
    dut.operation.value = SUBTRACTION
    
    # Test status_reg at IDLE
    assert int(dut.status_reg.value) == 0, "status_reg should be 0 at IDLE"
    
    payload = [100, 100, 0xFFFFFFFF]
    await send_packet(dut, payload)
    
    # Test status_reg while processing
    await ReadOnly()
    assert int(dut.status_reg.value) == 1, "status_reg should be 1 during processing"

    results = await receive_packet(dut)
    assert results[1] == 50, "Subtraction Word 1 failed"
    assert results[2] == 50, "Subtraction Word 0 failed"
    assert results[3] == 0xFFFFFFFF, "Footer missing"
    
    # Test return to IDLE
    await RisingEdge(dut.clk)
    assert int(dut.status_reg.value) == 0, "status_reg failed to return to 0"
    dut._log.info("Comprehensive Test Passed")

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