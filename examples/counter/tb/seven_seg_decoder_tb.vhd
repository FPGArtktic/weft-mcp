-- SPDX-License-Identifier: GPL-3.0-only
--
-- seven_seg_decoder_tb - checks all sixteen patterns and the blank input
--
-- Pure VHDL against a pure VHDL entity, so GHDL runs it without any Verilog
-- tooling. The full design cannot be simulated this way: the rest of the
-- hierarchy is SystemVerilog and Verilog, and GHDL reads neither.
--
library ieee;
use ieee.std_logic_1164.all;
use ieee.numeric_std.all;

entity seven_seg_decoder_tb is
end entity seven_seg_decoder_tb;

architecture sim of seven_seg_decoder_tb is

    type pattern_array is array (0 to 15) of std_logic_vector(6 downto 0);

    -- Active-high patterns; the unit emits them inverted.
    constant EXPECTED : pattern_array := (
        "0111111", "0000110", "1011011", "1001111",
        "1100110", "1101101", "1111101", "0000111",
        "1111111", "1101111", "1110111", "1111100",
        "0111001", "1011110", "1111001", "1110001"
    );

    signal nibble : std_logic_vector(3 downto 0) := (others => '0');
    signal blank  : std_logic := '0';
    signal seg_n  : std_logic_vector(6 downto 0);

begin

    dut : entity work.seven_seg_decoder
        port map (
            nibble => nibble,
            blank  => blank,
            seg_n  => seg_n
        );

    stimulus : process
        variable errors : natural := 0;
    begin
        blank <= '0';

        for i in EXPECTED'range loop
            nibble <= std_logic_vector(to_unsigned(i, nibble'length));
            wait for 10 ns;
            if seg_n /= not EXPECTED(i) then
                report "wrong pattern for nibble " & integer'image(i)
                    severity error;
                errors := errors + 1;
            end if;
        end loop;

        blank  <= '1';
        nibble <= "1010";
        wait for 10 ns;
        if seg_n /= "1111111" then
            report "blank does not blank the display" severity error;
            errors := errors + 1;
        end if;

        if errors = 0 then
            report "=== TEST PASSED ===" severity note;
        else
            report "=== TEST FAILED ===" severity failure;
        end if;

        wait;
    end process stimulus;

end architecture sim;
