from __future__ import annotations

import os
from pathlib import Path

import pytest
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import NextTimeStep, RisingEdge, ReadOnly, Timer, with_timeout
from cocotb_tools.runner import get_runner

NUM_PORT = 24
DATA_WIDTH = 64
KEEP_FULL = (1 << (DATA_WIDTH // 8)) - 1
KEEP_BYTES = DATA_WIDTH // 8


def _iverilog_patch_sv(src: str) -> str:
    """Icarus rejects a trailing comma after the last port; match that style if present."""
    patched = src.replace(
        "data_rx_keep_out      , //Keep signal aligned with the output data.",
        "data_rx_keep_out       //Keep signal aligned with the output data.",
    )
    if os.getenv("COCOTB_WAVES"):
        insert = (
            "\ninitial begin "
            '$dumpfile("ingress_arbiter.vcd"); '
            "$dumpvars(0, ingress_arbiter); "
            "end\n"
        )
        patched = patched.replace("endmodule", insert + "endmodule")
    return patched


def _write_ingress_wrap(proj_path: Path) -> Path:
    """Flatten unpacked arrays for cocotb+VPI (Icarus indexes unpacked ports as opaque blobs)."""
    build_dir = proj_path / "sim_build"
    p = build_dir / "ingress_arbiter_cocotb_wrap.sv"
    p.write_text(
        """\
`timescale 1ns / 1ps
// Cocotb-friendly top: one-dimensional vectors for all multi-port data (matches VPI access patterns).
module ingress_arbiter_cocotb_wrap #(
    parameter int DATA_WIDTH = 64,
    parameter int NUM_PORT  = 24
) (
    input  logic                                     clk,
    input  logic                                     rst,
    input  logic [NUM_PORT*DATA_WIDTH-1:0]           data_rx_in_flat,
    input  logic [NUM_PORT-1:0]                      data_rx_valid_in,
    input  logic [NUM_PORT-1:0]                      data_rx_start_in,
    input  logic [NUM_PORT-1:0]                      data_rx_last_in,
    input  logic [NUM_PORT*(DATA_WIDTH/8)-1:0]       data_rx_keep_in_flat,
    input  logic [NUM_PORT-1:0]                      port_data_rdy,
    input  logic [NUM_PORT-1:0]                      shared_mem_ram_full_in,
    output logic [NUM_PORT-1:0]                      port_data_rd_out,
    output logic [DATA_WIDTH-1:0]                    data_rx_out,
    output logic                                     data_rx_valid_out,
    output logic                                     data_rx_start_out,
    output logic                                     data_rx_last_out,
    output logic [(DATA_WIDTH/8)-1:0]                data_rx_keep_out
);
    wire [NUM_PORT-1:0][DATA_WIDTH-1:0]              data_rx_in;
    wire [NUM_PORT-1:0][(DATA_WIDTH/8)-1:0]          data_rx_keep_in;
    genvar gi;
    generate
        for (gi = 0; gi < NUM_PORT; gi = gi + 1) begin : g_pack
            assign data_rx_in[gi] = data_rx_in_flat[gi*DATA_WIDTH +: DATA_WIDTH];
            assign data_rx_keep_in[gi] = data_rx_keep_in_flat[gi*(DATA_WIDTH/8) +: (DATA_WIDTH/8)];
        end
    endgenerate

    ingress_arbiter #(
        .DATA_WIDTH(DATA_WIDTH),
        .NUM_PORT(NUM_PORT)
    ) u_dut (
        .clk(clk),
        .rst(rst),
        .data_rx_in(data_rx_in),
        .data_rx_valid_in(data_rx_valid_in),
        .data_rx_start_in(data_rx_start_in),
        .data_rx_last_in(data_rx_last_in),
        .data_rx_keep_in(data_rx_keep_in),
        .port_data_rdy(port_data_rdy),
        .shared_mem_ram_full_in(shared_mem_ram_full_in),
        .port_data_rd_out(port_data_rd_out),
        .data_rx_out(data_rx_out),
        .data_rx_valid_out(data_rx_valid_out),
        .data_rx_start_out(data_rx_start_out),
        .data_rx_last_out(data_rx_last_out),
        .data_rx_keep_out(data_rx_keep_out)
    );
endmodule
"""
    )
    return p


def _patched_dut_path(proj_path: Path) -> Path:
    """Copy ``sources/ingress_arbiter.sv`` into sim_build with Icarus-friendly port list."""
    build_dir = proj_path / "sim_build"
    build_dir.mkdir(parents=True, exist_ok=True)
    rtl = (proj_path / "sources/ingress_arbiter.sv").read_text()
    patched = _iverilog_patch_sv(rtl)
    out = build_dir / "ingress_arbiter_iv.v"
    out.write_text(patched)
    return out


def _uint_from_logic(dut_sig) -> int | None:
    """Unsigned sample for ``data_rx_out``; ``None`` if the bus still has X/Z."""
    try:
        v = int(dut_sig.value)
    except ValueError:
        return None
    return v & ((1 << DATA_WIDTH) - 1)


def _set_data_rx_word(dut, port: int, word: int) -> None:
    """Port ``port`` maps to ``data_rx_in_flat[port*W +: W]`` (see ``ingress_arbiter_cocotb_wrap``)."""
    m = (1 << DATA_WIDTH) - 1
    cur = int(dut.data_rx_in_flat.value)
    sh = port * DATA_WIDTH
    cur = (cur & ~(m << sh)) | ((word & m) << sh)
    dut.data_rx_in_flat.value = cur


def _set_data_rx_keep(dut, port: int, keep: int) -> None:
    km = (1 << KEEP_BYTES) - 1
    cur = int(dut.data_rx_keep_in_flat.value)
    sh = port * KEEP_BYTES
    cur = (cur & ~(km << sh)) | ((keep & km) << sh)
    dut.data_rx_keep_in_flat.value = cur


def _idle_all_ports(dut) -> None:
    dut.port_data_rdy.value = 0
    dut.shared_mem_ram_full_in.value = 0
    dut.data_rx_in_flat.value = 0
    dut.data_rx_valid_in.value = 0
    dut.data_rx_start_in.value = 0
    dut.data_rx_last_in.value = 0
    dut.data_rx_keep_in_flat.value = 0


async def start_clock(dut) -> None:
    """10 ns period (each pytest+cocotb run is a fresh simulation — see runner filter)."""
    dut.clk.value = 0
    clk = Clock(dut.clk, 10, unit="ns", impl="py")
    clk.start(start_high=False)


async def reset_dut(dut) -> None:
    dut.rst.value = 1
    _idle_all_ports(dut)
    await Timer(25, unit="ns")
    dut.rst.value = 0
    await RisingEdge(dut.clk)


def _drive_beat(dut, port: int, word: int, is_first: bool, is_last: bool) -> None:
    _set_data_rx_word(dut, port, word)
    dut.data_rx_valid_in.value = 1 << port
    dut.data_rx_start_in.value = (1 << port) if is_first else 0
    dut.data_rx_last_in.value = (1 << port) if is_last else 0
    _set_data_rx_keep(dut, port, KEEP_FULL)


def _clear_port_stream(dut, port: int) -> None:
    _set_data_rx_word(dut, port, 0)
    dut.data_rx_valid_in.value = 0
    dut.data_rx_start_in.value = 0
    dut.data_rx_last_in.value = 0
    dut.data_rx_keep_in_flat.value = 0


async def send_packet(dut, port: int, words: list[int]) -> None:
    """Drive one packet on ``port``."""
    assert len(words) >= 1
    n = len(words)
    dut.port_data_rdy.value = 1 << port

    if n == 1:
        w = words[0]
        _drive_beat(dut, port, w, True, True)
        await RisingEdge(dut.clk)
        _drive_beat(dut, port, w, True, True)
        for _ in range(500):
            await RisingEdge(dut.clk)
            await ReadOnly()
            saw_last = int(dut.data_rx_last_out.value) and int(dut.data_rx_valid_out.value)
            await NextTimeStep()
            _drive_beat(dut, port, w, True, True)
            if saw_last:
                break
    else:
        w0 = words[0]
        _drive_beat(dut, port, w0, True, False)
        await RisingEdge(dut.clk)
        _drive_beat(dut, port, w0, True, False)
        wm0 = w0 & ((1 << DATA_WIDTH) - 1)
        saw_first = False
        for _ in range(500):
            await RisingEdge(dut.clk)
            await ReadOnly()
            vo = int(dut.data_rx_valid_out.value)
            d0 = _uint_from_logic(dut.data_rx_out)
            await NextTimeStep()
            _drive_beat(dut, port, w0, True, False)
            if vo and d0 is not None and d0 == wm0:
                saw_first = True
                break
        if not saw_first:
            raise AssertionError("multi-beat: first flit never forwarded")
        for idx in range(1, n):
            await NextTimeStep()
            _drive_beat(dut, port, words[idx], False, idx == n - 1)
            await RisingEdge(dut.clk)
        await NextTimeStep()
        _drive_beat(dut, port, words[-1], False, True)
        await RisingEdge(dut.clk)

    await RisingEdge(dut.clk)
    await NextTimeStep()
    _clear_port_stream(dut, port)
    dut.port_data_rdy.value = 0


async def receive_packet(dut) -> list[tuple[int, int, int, int]]:
    """Sample muxed outputs until a beat with ``last`` set."""
    beats: list[tuple[int, int, int, int]] = []

    async def _collect() -> None:
        while True:
            await RisingEdge(dut.clk)
            await ReadOnly()
            if int(dut.data_rx_valid_out.value):
                d = _uint_from_logic(dut.data_rx_out)
                if d is None:
                    continue
                s = int(dut.data_rx_start_out.value)
                l = int(dut.data_rx_last_out.value)
                k = int(dut.data_rx_keep_out.value)
                beats.append((d, s, l, k))
                if l:
                    return

    await with_timeout(_collect(), 50, "us")
    return beats


@cocotb.test()
async def basic_arbiter_single_port_short_packet(dut):
    """Basic: one port, single-beat packet; check muxed output and start/last."""
    await start_clock(dut)
    await reset_dut(dut)

    payload = [0xDEAD_BEEF_CAFE_BABE]
    rx = cocotb.start_soon(receive_packet(dut))
    tx = cocotb.start_soon(send_packet(dut, 0, payload))
    out = await rx
    await tx
    assert len(out) == 1
    assert out[0][0] == payload[0]
    assert out[0][1] == 1, "start should be set on first (only) beat"
    assert out[0][2] == 1, "last should be set on single-beat packet"
    assert out[0][3] == KEEP_FULL
    dut._log.info("Basic arbiter test passed")


@cocotb.test()
async def medium_arbiter_multi_beat_packet(dut):
    """Medium: one port, several beats; verify order and start/last placement."""
    await start_clock(dut)
    await reset_dut(dut)
    payload = [
        0x0000_0000_0000_0001,
        0x0000_0000_0000_0002,
        0x0000_0000_0000_0003,
        0x0000_0000_0000_0004,
    ]
    rx = cocotb.start_soon(receive_packet(dut))
    tx = cocotb.start_soon(send_packet(dut, 0, payload))
    out = await rx
    await tx
    datas = [b[0] for b in out]
    assert datas == payload
    assert out[0][1] == 1 and out[0][2] == 0
    assert out[-1][2] == 1 and out[-1][1] == 0
    dut._log.info("Medium multi-beat test passed")


@cocotb.test()
async def comprehensive_arbiter_round_robin_and_ram_full(dut):
    """Comprehensive: round-robin across two ports; shared_mem_ram_full blocks grant."""
    await start_clock(dut)
    await reset_dut(dut)

    await RisingEdge(dut.clk)
    dut.shared_mem_ram_full_in.value = 0

    r0 = cocotb.start_soon(receive_packet(dut))
    t0 = cocotb.start_soon(send_packet(dut, 0, [0xAAA0]))
    first = await r0
    await t0
    assert first[0][0] == 0xAAA0

    r1 = cocotb.start_soon(receive_packet(dut))
    t1 = cocotb.start_soon(send_packet(dut, 1, [0xBBB0]))
    second = await r1
    await t1
    assert second[0][0] == 0xBBB0

    await reset_dut(dut)
    await RisingEdge(dut.clk)
    full = 1 << 3
    dut.shared_mem_ram_full_in.value = full
    dut.port_data_rdy.value = 1 << 3
    for _ in range(80):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert int(dut.data_rx_valid_out.value) == 0, "arbiter must not grant when RAM full"

    await NextTimeStep()
    dut.shared_mem_ram_full_in.value = 0
    r3 = cocotb.start_soon(receive_packet(dut))
    t3 = cocotb.start_soon(send_packet(dut, 3, [0xCCCC_CCCC_CCCC_CCCC]))
    third = await r3
    await t3
    assert third[0][0] == 0xCCCC_CCCC_CCCC_CCCC

    dut._log.info("Comprehensive round-robin / RAM-full test passed")


@pytest.mark.parametrize(
    "cocotb_case",
    [
        "basic_arbiter_single_port_short_packet",
        "medium_arbiter_multi_beat_packet",
        "comprehensive_arbiter_round_robin_and_ram_full",
    ],
)
def test_ingress_arbiter_hidden_runner(cocotb_case):
    """Run each cocotb scenario in its own simulation (one ``@cocotb.test`` per vvp)."""
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    sources = [_patched_dut_path(proj_path), _write_ingress_wrap(proj_path)]
    runner = get_runner(sim)
    runner.build(
        sources=sources,
        hdl_toplevel="ingress_arbiter_cocotb_wrap",
        always=True,
    )
    prev = os.environ.get("COCOTB_TEST_FILTER")
    os.environ["COCOTB_TEST_FILTER"] = cocotb_case
    try:
        runner.test(hdl_toplevel="ingress_arbiter_cocotb_wrap", test_module="test_ingress_arbiter_hidden")
    finally:
        if prev is None:
            os.environ.pop("COCOTB_TEST_FILTER", None)
        else:
            os.environ["COCOTB_TEST_FILTER"] = prev
