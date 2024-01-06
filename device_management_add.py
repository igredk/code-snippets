async def device_management_add_service(payload: DeviceManagementAddServiceRequest) -> None:
    user_devices: Optional[DeviceManagement] = await DeviceManagement.get_from_db(payload.user_id)

    if not user_devices:
        await DeviceManagement.create_user(payload.user_id, payload.udid)
        return

    existing_device: Optional[Device] = next(
        (device for device in user_devices.devices if device.udid == payload.udid), None
    )
    if not existing_device:
        await user_devices.add_device(payload.udid)
        asyncio.create_task(
            send_push_to_users_devices(
                user_id=payload.user_id,
                udid_from_request=payload.udid,
                brand=payload.brand,
                model=payload.model,
                devices=user_devices.devices,
                push_type=PushType.NEW_DEVICE_REGISTERED,
            )
        )
        return

    if existing_device.status_info.status is not DeviceStatus.TRUSTED:
        customer_details: GetCustomerDetailsResponse = await CustomerDetailsGetter(payload.user_id).get()
        asyncio.create_task(
            create_contract_event(
                CreateContractEventRequest(egn=customer_details.pinegn, doc_no=customer_details.mobile_phone_number)
            )
        )
        asyncio.create_task(
            send_push_to_users_devices(
                user_id=payload.user_id,
                udid_from_request=payload.udid,
                brand=payload.brand,
                model=payload.model,
                devices=user_devices.devices,
                push_type=PushType.NEW_DEVICE_REGISTERED,
            )
        )

    if existing_device.status_info.status is DeviceStatus.DELETED:
        await user_devices.change_device_status(device_to_update=existing_device, status=DeviceStatus.IN_PROGRESS)
