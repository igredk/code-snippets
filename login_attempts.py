from __future__ import annotations

from datetime import datetime
from typing import Dict
from typing import Final
from typing import List
from typing import Optional
from typing import Union

import settings

from exceptions import UnregisteredDeviceException
from integrations.notifications import api as notification_api
from integrations.notifications.entities import Device as NotificationDevice
from integrations.notifications.entities import GetDeviceListRequest
from integrations.notifications.entities import GetDeviceListResponse
from integrations.redis_client import Redis
from services.device_management.entities import DeviceManagement
from services.device_management.entities import PinTry
from settings import DATE_FORMAT
from utils.schemas.base import PydanticBaseModel

INITIAL_PAGE: Final[int] = 1
PAGE_LIMIT: Final[int] = 20
LOGIN_ATTEMPT_KEY_PREFIX: Final[str] = 'device/loginAttempt/getList:'


class GetLoginAttemptListServiceRequest(PydanticBaseModel):
    user_id: str
    page: int = INITIAL_PAGE
    limit: int = PAGE_LIMIT


class Device(PydanticBaseModel):
    udid: Optional[str]
    os_version: Optional[str]
    brand_name: Optional[str]
    model: Optional[str]
    os: Optional[str]


class LoginAttempt(PydanticBaseModel):
    attempt_date: str
    device_info: Optional[Device]


class GetLoginAttemptListServiceResponse(PydanticBaseModel):
    attempts: List[LoginAttempt] = []


class CachedLoginAttemptList(GetLoginAttemptListServiceResponse):
    @staticmethod
    def get_key(user_id: str) -> str:
        return f'{LOGIN_ATTEMPT_KEY_PREFIX}{user_id}'

    async def save_to_db(self, user_id: str) -> None:
        await Redis.set(self.get_key(user_id), self.json(), expire=settings.CACHE_TTL['loginAttemptList'])

    @classmethod
    async def get_from_db(cls, user_id: str) -> Optional[CachedLoginAttemptList]:
        cache: Optional[str] = await Redis.get(cls.get_key(user_id))
        if not cache:
            return None
        return cls.parse_raw(cache)

    @classmethod
    async def delete_from_db(cls, user_id: str) -> None:
        await Redis.delete(cls.get_key(user_id))


async def get_login_attempt_list_service(
    payload: GetLoginAttemptListServiceRequest,
) -> GetLoginAttemptListServiceResponse:
    cached_login_attempts: Optional[CachedLoginAttemptList] = await CachedLoginAttemptList.get_from_db(payload.user_id)
    if cached_login_attempts:
        return GetLoginAttemptListServiceResponse(
            attempts=(
                cached_login_attempts.attempts[(payload.page - 1) * payload.limit : payload.page * payload.limit]
                if payload.page != INITIAL_PAGE
                else cached_login_attempts.attempts[: payload.limit]
            )
        )

    user_devices: Optional[DeviceManagement] = await DeviceManagement.get_from_db(payload.user_id)
    if not user_devices:
        raise UnregisteredDeviceException

    pin_tries: Dict[str, List[Union[List[PinTry], NotificationDevice]]] = {
        device.udid: [device.pin_tries] for device in user_devices.devices if device.pin_tries
    }
    if not pin_tries:
        return GetLoginAttemptListServiceResponse()

    device_info_list: GetDeviceListResponse = await notification_api.get_device_list(
        GetDeviceListRequest(user_id=payload.user_id, udids=list(pin_tries))
    )
    for device in device_info_list.devices:
        pin_tries[device.udid].append(device)

    login_attempts: List[LoginAttempt] = [
        LoginAttempt(
            attempt_date=login_attempt.attempt_date.strftime(DATE_FORMAT),
            device_info=Device.parse_obj(devices_by_udid[1].dict()) if devices_by_udid[1:] else None,  # type: ignore
        )
        for devices_by_udid in pin_tries.values()
        for login_attempt in devices_by_udid[0]
    ]
    login_attempts.sort(key=lambda attempt: datetime.strptime(attempt.attempt_date, DATE_FORMAT), reverse=True)

    await CachedLoginAttemptList(attempts=login_attempts).save_to_db(payload.user_id)

    return GetLoginAttemptListServiceResponse(
        attempts=(
            login_attempts[(payload.page - 1) * payload.limit : payload.page * payload.limit]
            if payload.page != INITIAL_PAGE
            else login_attempts[: payload.limit]
        )
    )
