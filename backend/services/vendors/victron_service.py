

from pymodbus.client import AsyncModbusTcpClient
from backend.utils.modbus_decoders import decode_signed_16, decode_signed_32


class VictronService:

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    async def _connect(self):
        client = AsyncModbusTcpClient(self.host, port=self.port)
        await client.connect()
        return client

    # 🔋 Battery
    async def read_battery(self, slave: int):
        client = await self._connect()

        result = await client.read_input_registers(259, count=10, slave=slave)
        if result.isError():
            await client.close()
            raise Exception("Battery read error")

        r = result.registers

        data = {
            "soc": r[0] / 10,
            "voltage": r[1] / 100,
            "current": decode_signed_16(r[2]) / 10,
            "temperature": r[3] / 10,
            "power": decode_signed_16(r[4]),
            "soh": r[5] / 10
        }

        await client.close()
        return data

    # ⚡ ESS AC
    async def read_ess_ac(self, slave: int):
        client = await self._connect()

        start = 3
        count = 18

        result = await client.read_input_registers(start, count=count, slave=slave)
        if result.isError():
            await client.close()
            raise Exception("ESS AC read error")

        r = result.registers

        await client.close()

        return {
            "input_voltage_l1": r[0] / 10,
            "input_voltage_l2": r[1] / 10,
            "input_voltage_l3": r[2] / 10,
        }

    # 🔌 VE.Bus
    async def read_vebus(self, slave: int):
        client = await self._connect()

        result = await client.read_input_registers(21, count=21, slave=slave)
        if result.isError():
            await client.close()
            raise Exception("VE.Bus read error")

        r = result.registers

        await client.close()

        return {
            "output_frequency_hz": decode_signed_16(r[0]) / 100,
            "soc_percent": r[9] / 10,
        }

    # 🚀 Full poll
    async def full_poll(self, slave: int):
        return {
            "battery": await self.read_battery(slave),
            "ess_ac": await self.read_ess_ac(slave),
            "vebus": await self.read_vebus(slave),
        }
    

    