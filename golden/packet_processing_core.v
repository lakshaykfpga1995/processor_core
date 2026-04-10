`timescale 1ns / 1ps

module packet_processing_core (
    input wire clk,
    input wire rst_n,
    // AXI stream interface for input
    input wire [31:0] s_axis_tdata,
    input wire s_axis_tvalid,
    output reg s_axis_tready,
    // AXI stream interface for output
    output reg [31:0] m_axis_tdata,
    output wire m_axis_tvalid,
    input wire m_axis_tready,
    // Control and status signals
    input wire [31:0] control_reg,
    output wire status_reg
);

    // Memory and Internal registers
    reg [31:0] packet_buffer [0:255];
    reg [7:0]  packet_length;
    reg [31:0] internal_reg1;
    reg [31:0] internal_reg2;

    // State machine parameters
    localparam STATE_IDLE     = 2'b00;
    localparam STATE_PROCESS  = 2'b01;
    localparam STATE_TRANSMIT = 2'b10;

    reg [1:0] current_state;
    reg [1:0] next_state;

    // 1. Sequential State Transition
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            current_state <= STATE_IDLE;
        end else begin
            current_state <= next_state;
        end
    end

    // 2. Combinational Next State Logic
    always @(*) begin
        next_state = current_state;
        case (current_state)
            STATE_IDLE: begin
                // Move to PROCESS only when EOP marker (0xFF) is received
                if (s_axis_tvalid && s_axis_tready && s_axis_tdata[31:24] == 8'hFF)
                    next_state = STATE_PROCESS;
            end
            STATE_PROCESS: begin
                next_state = STATE_TRANSMIT;
            end
            STATE_TRANSMIT: begin
                // Return to IDLE once buffer is empty and last word is accepted
                if (m_axis_tready && packet_length == 0)
                    next_state = STATE_IDLE;
            end
            default: next_state = STATE_IDLE;
        endcase
    end

    // 3. Sequential Data Path Logic
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            packet_length <= 0;
            internal_reg1 <= 0;
            internal_reg2 <= 0;
            m_axis_tdata  <= 0;
        end else begin
            case (current_state)
                STATE_IDLE: begin
                    if (s_axis_tvalid && s_axis_tready) begin
                        packet_buffer[packet_length] <= s_axis_tdata;
                        packet_length <= packet_length + 1;
                    end
                end

                STATE_PROCESS: begin
                    // Perform arithmetic using control_reg
                    internal_reg1 <= packet_buffer[0] + control_reg;
                    internal_reg2 <= packet_buffer[1] * control_reg;
                    
                    // Update buffer with processed values
                    packet_buffer[0] <= packet_buffer[0] + control_reg;
                    packet_buffer[1] <= packet_buffer[1] * control_reg;
                end

                STATE_TRANSMIT: begin
                    if (m_axis_tready) begin
                        if (packet_length > 0) begin
                            m_axis_tdata <= packet_buffer[packet_length - 1];
                            packet_length <= packet_length - 1;
                        end else begin
                            m_axis_tdata <= 32'hFFFFFFFF; // Transmission Footer
                        end
                    end
                end
            endcase
        end
    end

    // 4. Output Assignments
    always @(*) begin
        s_axis_tready = (current_state == STATE_IDLE);
    end

    assign m_axis_tvalid = (current_state == STATE_TRANSMIT);
    assign status_reg    = (packet_length > 0);

endmodule