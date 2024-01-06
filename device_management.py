from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List
from typing import Optional

from aiomongodel import DocumentNotFoundError
from pydantic import Field

from exceptions import UnregisteredDeviceException
from integrations.mongodb.models import DeviceManagementMongo
from settings import SAVE_COUNT_LOGIN_ATTEMPTS
from utils.datetime_tools import get_current_datetime
from utils.schemas.base import PydanticBaseModel


class DeviceStatus(str, Enum):
    TRUSTED = 'trusted'
    UNTRUSTED = 'untrusted'
    IN_PROGRESS = 'inProgress'
    DELETED = 'deleted'

    def __str__(self) -> str:
        return self.value


class PinTry(PydanticBaseModel):
    attempt_date: datetime = Field(default_factory=get_current_datetime)
    blocked: bool


class DeviceStatusInfo(PydanticBaseModel):
    status: DeviceStatus
    licensor_udid: Optional[str]
    update_time: datetime = Field(default_factory=get_current_datetime)


class Device(PydanticBaseModel):
    udid: str
    status_info: DeviceStatusInfo
    pin_tries: List[PinTry] = []


class DeviceManagement(PydanticBaseModel):
    user_id: str
    devices: List[Device]

    @classmethod
    async def create_user(cls, user_id: str, udid: str, is_blocked: Optional[bool] = None) -> None:
        await cls(
            user_id=user_id,
            devices=[
                Device(
                    udid=udid,
                    status_info=DeviceStatusInfo(status=DeviceStatus.TRUSTED, licensor_udid=udid),
                    pin_tries=[PinTry(blocked=is_blocked)] if is_blocked is not None else [],
                )
            ],
        ).save()

    async def add_device(self, udid: str, is_blocked: Optional[bool] = None) -> None:
        self.devices.append(
            Device(
                udid=udid,
                status_info=DeviceStatusInfo(status=DeviceStatus.IN_PROGRESS),
                pin_tries=[PinTry(blocked=is_blocked)] if is_blocked is not None else [],
            )
        )
        await self.save()

    async def add_login_attempt(self, udid: str, is_blocked: bool) -> None:
        existing_device: Optional[Device] = next((device for device in self.devices if device.udid == udid), None)
        if not existing_device:
            raise UnregisteredDeviceException

        if len(existing_device.pin_tries) < SAVE_COUNT_LOGIN_ATTEMPTS:
            existing_device.pin_tries.append(PinTry(blocked=is_blocked))
        # if count will be decreased in CICD we need to truncate pin tries array to match new value
        elif len(existing_device.pin_tries) > SAVE_COUNT_LOGIN_ATTEMPTS:
            existing_device.pin_tries = existing_device.pin_tries[:SAVE_COUNT_LOGIN_ATTEMPTS]
            existing_device.pin_tries[-1] = PinTry(blocked=is_blocked)
        else:
            existing_device.pin_tries[-1] = PinTry(blocked=is_blocked)
        await self.save()

    async def update_device(self, udid: str, licensor_udid: str, status: DeviceStatus) -> None:
        updated_devices: List[Device] = [device for device in self.devices if device.udid != udid]
        updated_devices.append(
            Device(udid=udid, status_info=DeviceStatusInfo(status=status, licensor_udid=licensor_udid))
        )
        self.devices = updated_devices
        await self.save()

    async def change_device_status(self, device_to_update: Device, status: DeviceStatus) -> None:
        updated_devices: List[Device] = [device for device in self.devices if device.udid != device_to_update.udid]

        updated_device: Device = device_to_update
        updated_device.status_info.status = status

        updated_devices.append(updated_device)
        self.devices = updated_devices
        await self.save()

    async def save(self) -> None:
        await DeviceManagementMongo.q().update_one(
            {
                DeviceManagementMongo.user_id.s: self.user_id,
            },
            {
                '$set': self.dict(by_alias=False),
            },
            upsert=True,
        )

    @classmethod
    async def get_from_db(cls, user_id: str) -> Optional[DeviceManagement]:
        try:
            user_devices: DeviceManagementMongo = await DeviceManagementMongo.q().find_one(
                {DeviceManagementMongo.user_id.s: user_id}
            )
        except DocumentNotFoundError:
            return None

        return cls(**user_devices.to_data())
