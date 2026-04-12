`timescale 1ns / 1ps
// Baseline: module shell only — implementation intentionally missing (outputs undriven / wrong).
module ingress_arbiter #(
   parameter DATA_WIDTH      = 64,
   parameter NUM_PORT        = 24)
   (
   input  logic                                     clk                   ,
   input  logic                                     rst                   ,
   input  logic [NUM_PORT-1:0][DATA_WIDTH-1:0]      data_rx_in            ,
   input  logic [NUM_PORT-1:0]                      data_rx_valid_in      ,
   input  logic [NUM_PORT-1:0]                      data_rx_start_in      ,
   input  logic [NUM_PORT-1:0]                      data_rx_last_in       ,
   input  logic [NUM_PORT-1:0][(DATA_WIDTH/8)-1:0]  data_rx_keep_in       ,
   input  logic [NUM_PORT-1:0]                      port_data_rdy         ,
   input logic  [NUM_PORT-1:0]                      shared_mem_ram_full_in,
   output logic [NUM_PORT-1:0]                      port_data_rd_out      ,
   output logic [DATA_WIDTH-1:0]                    data_rx_out           ,
   output logic                                     data_rx_valid_out     ,
   output logic                                     data_rx_start_out     ,
   output logic                                     data_rx_last_out      ,
   output logic [(DATA_WIDTH/8)-1:0]                data_rx_keep_out
   );

endmodule
