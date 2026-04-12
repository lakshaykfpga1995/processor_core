`timescale 1ns / 1ps
module ingress_arbiter #(
   parameter DATA_WIDTH      = 64,
   parameter NUM_PORT        = 24)
   (
//Inputs from the ingress buffer   
   input  logic                                     clk                   ,  
   input  logic                                     rst                   ,
   input  logic [NUM_PORT-1:0][DATA_WIDTH-1:0]      data_rx_in            , //Input data bus signal.
   input  logic [NUM_PORT-1:0]                      data_rx_valid_in      , //Valid signal input.  
   input  logic [NUM_PORT-1:0]                      data_rx_start_in      , //Signal indicates the start of packet.
   input  logic [NUM_PORT-1:0]                      data_rx_last_in       , //Signal indicates the last of packet. 
   input  logic [NUM_PORT-1:0][(DATA_WIDTH/8)-1:0]  data_rx_keep_in       , //Indcates bytes valid of data_rx_in, each byte in data corresponds to each bit in keep signals.


//Input from Ingress Buffer
   input  logic [NUM_PORT-1:0]                      port_data_rdy         , //Signal indicates that corresponding port is ready with at least 1 packet to transmit.   
   
   input logic  [NUM_PORT-1:0]                      shared_mem_ram_full_in,

//Output to Ingress Buffer
   output logic [NUM_PORT-1:0]                      port_data_rd_out      , //Read enable signal to the port buffer to read the packets.

//Output to Packet Classifier
   output logic [DATA_WIDTH-1:0]                    data_rx_out           , //Output data bus
   output logic                                     data_rx_valid_out     , //Valid out signal
   output logic                                     data_rx_start_out     , //Start signal indicating the start of the output data.
   output logic                                     data_rx_last_out      , //Last signal indicating the end of output data.
   output logic [(DATA_WIDTH/8)-1:0]                data_rx_keep_out       //Keep signal aligned with the output data.

   
   );
   
   localparam MIN_CLK_COUNT = 7   ; //Minimum clock cycle need to be present to transmit the another packet after first packet transmittion completed.
   logic port_rd_en_flag           ; //Flag indicates that the port is active and data read is active.
   logic [3:0] min_clk_count       ; //Counter which counts the minimum clock transfer requirement.
   logic [4:0] current_port        ; 
   logic [4:0] next_port           ;
   logic [NUM_PORT-1:0] port_data_rd_reg ;
 
   typedef enum logic [2:0]{
      IDLE              , 
      WAIT_FOR_PORT_RDY , //State where the search for active port happens.
      READ_DATA         , //State where data from port is been read. 
      SHORT_PKT_WAIT    , //State where some dead clock is been introduced for short packets which is less than 7 clock cycle.
      DATA_LAST         , //Last clock data is been read out.          
      DEAD_CLK            //1 Clock of dead cycle is been introduced between reading of two packets.
   }arbiter_state ;
   arbiter_state state,next_state;
     
    
   //State machine to read data from port in round-robin manner.
   always_comb begin 
      case (state)
         IDLE : begin
            next_state = WAIT_FOR_PORT_RDY ;            
         end
         WAIT_FOR_PORT_RDY : begin
            if(port_rd_en_flag)
               next_state = READ_DATA ;
            else 
               next_state = WAIT_FOR_PORT_RDY ;
         end
         READ_DATA : begin
            //Pkt data last happens for this port data
            if (data_rx_last_in[current_port]) 
               next_state = DATA_LAST ;
            else 
               next_state = READ_DATA ;         
         end
         DATA_LAST : begin
         //Pkt data is of more than 7 clocks
            if (min_clk_count == MIN_CLK_COUNT) 
               next_state = DEAD_CLK ;
         //Pkt data is of less than 7 clocks
            else 
               next_state = SHORT_PKT_WAIT ;    
         end
         SHORT_PKT_WAIT : begin
            if (min_clk_count == MIN_CLK_COUNT)
               next_state = DEAD_CLK ;         
            else 
               next_state = SHORT_PKT_WAIT ;
         end
         DEAD_CLK : begin
            next_state = WAIT_FOR_PORT_RDY ;            
         end
         default : begin
            next_state = IDLE ;
         end
      endcase
   end
   
   always_ff @(posedge clk) begin
      if(rst)
         state <= IDLE ;
      else           
         state <= next_state ;    
   end
   
   always_ff @(posedge clk) begin
      if(rst) begin
         current_port      <= 5'd0 ;
         next_port         <= 5'd0 ;
         port_rd_en_flag   <= 1'b0 ;
         min_clk_count     <= 4'd0 ;
         data_rx_valid_out <= 1'b0 ;
         data_rx_start_out <= 1'b0 ;
         data_rx_last_out  <= 1'b0 ;
         data_rx_keep_out  <= {(DATA_WIDTH/8){1'b0}} ;
         port_data_rd_reg  <= {NUM_PORT{1'b0}} ;
      end
      else begin 
         case (next_state) 
            IDLE : begin 
               current_port      <= 5'd0 ;
               next_port         <= 5'd0 ;
               port_rd_en_flag   <= 1'b0 ;
               min_clk_count     <= 4'd0 ;
            end
            WAIT_FOR_PORT_RDY : begin
               if (port_data_rdy[next_port] & !shared_mem_ram_full_in[next_port]  & !port_rd_en_flag) begin
                  current_port     <= next_port;    
                  port_rd_en_flag  <= 1'b1 ; 
                  //port_data_rd_reg <= 1 << next_port ;
                  
                   for (int i = 0; i < NUM_PORT; i++) begin
                        if (next_port == i)
                            port_data_rd_reg[i] <= 1'b1;
                        else
                            port_data_rd_reg[i] <= 1'b0;
                    end
               end
               else begin
                  // Wrap around if at the last port
                  if (next_port == NUM_PORT-1) begin
                     next_port <= 5'd0;
                  end
                  else begin
                     next_port <= next_port + 1'b1;
                  end
               end
               
            end
            READ_DATA : begin              
               data_rx_out        <= data_rx_in[current_port] ;    
               data_rx_valid_out  <= data_rx_valid_in[current_port] ;
               data_rx_start_out  <= data_rx_start_in[current_port] ;
               data_rx_last_out   <= data_rx_last_in[current_port] ;
               data_rx_keep_out   <= data_rx_keep_in[current_port] ;


               
               if (data_rx_valid_in[current_port]) begin
                  if (min_clk_count < MIN_CLK_COUNT)
                    min_clk_count <= min_clk_count + 1; 
               end     
            end
            DATA_LAST : begin              
               data_rx_out        <= data_rx_in[current_port] ;    
               data_rx_valid_out  <= data_rx_valid_in[current_port] ;
               data_rx_start_out  <= data_rx_start_in[current_port] ;
               data_rx_last_out   <= data_rx_last_in [current_port] ;
               data_rx_keep_out   <= data_rx_keep_in[current_port] ;

               port_data_rd_reg   <=  {NUM_PORT{1'b0}};
               
               if (data_rx_valid_in[current_port]) begin
                  if (min_clk_count < MIN_CLK_COUNT)
                    min_clk_count <= min_clk_count + 1; 
               end     
            end
            SHORT_PKT_WAIT : begin        
               data_rx_out        <= {DATA_WIDTH{1'b0}} ; 
               data_rx_valid_out  <= 1'b0 ;
               data_rx_start_out  <= 1'b0 ;
               data_rx_last_out   <= 1'b0 ;
               data_rx_keep_out   <= {(DATA_WIDTH/8){1'b0}} ;

               port_rd_en_flag    <= 1'b0 ;

              
               if (min_clk_count < MIN_CLK_COUNT)
                 min_clk_count <= min_clk_count + 1; 
            end
            DEAD_CLK : begin
               data_rx_out          <= {DATA_WIDTH{1'b0}} ;    
               data_rx_valid_out    <= 1'b0 ;
               data_rx_start_out    <= 1'b0 ;
               data_rx_last_out     <= 1'b0 ;
               data_rx_keep_out     <= {(DATA_WIDTH/8){1'b0}} ;

               next_port            <= (current_port == NUM_PORT-1) ? 5'd0 : current_port + 1;
               port_rd_en_flag      <= 1'b0 ;
               min_clk_count        <= 4'd0 ;
               port_data_rd_reg     <= {NUM_PORT{1'b0}} ;

            end
            default: begin
            end
         endcase    
      end     
   end
   
   //Deasserting the rd_out signal in the same clock as we rcved te data_last_in signal.
   always_comb begin
      for (int i=0; i<NUM_PORT; i++) begin
         port_data_rd_out[i] = port_data_rd_reg[i] && !data_rx_last_in[i] ; 
      end 
   end
  
endmodule
