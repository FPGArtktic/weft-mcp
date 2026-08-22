-- SPDX-License-Identifier: GPL-2.0-only
--
-- seven_seg_decoder - hexadecimal digit to seven-segment patterns
-- @nibble: value to display, 0 to F
-- @blank:  high blanks the display entirely
-- @seg_n:  active-low segments, ordered (g, f, e, d, c, b, a)
--
-- Outputs are active low, as a common-anode display needs. The patterns are
-- held active high internally and inverted once on the way out, which keeps
-- the table readable.
--
-- Deliberately written in VHDL: the rest of the design is SystemVerilog and
-- Verilog-2001, so this entity is what forces the toolchain to parse and
-- elaborate across language boundaries.
--
library ieee;
use ieee.std_logic_1164.all;

entity seven_seg_decoder is
    port (
        nibble : in  std_logic_vector(3 downto 0);
        blank  : in  std_logic;
        seg_n  : out std_logic_vector(6 downto 0)
    );
end entity seven_seg_decoder;

architecture rtl of seven_seg_decoder is

    signal seg : std_logic_vector(6 downto 0);      -- active high

begin

    with nibble select seg <=
        "0111111" when "0000",
        "0000110" when "0001",
        "1011011" when "0010",
        "1001111" when "0011",
        "1100110" when "0100",
        "1101101" when "0101",
        "1111101" when "0110",
        "0000111" when "0111",
        "1111111" when "1000",
        "1101111" when "1001",
        "1110111" when "1010",
        "1111100" when "1011",
        "0111001" when "1100",
        "1011110" when "1101",
        "1111001" when "1110",
        "1110001" when "1111",
        "0000000" when others;

    seg_n <= "1111111" when blank = '1' else not seg;

end architecture rtl;
