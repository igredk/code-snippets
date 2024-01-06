import asyncio

from typing import Optional

from exceptions import StorylyNotAvailableException
from integrations.redis_client import Redis
from integrations.storyly.api import storyly_instance
from integrations.storyly.api import storyly_story_group
from integrations.storyly.entities import InstanceTitle
from integrations.storyly.entities import StorylyInstance
from integrations.storyly.entities import StorylyInstanceResponse
from integrations.storyly.entities import StorylyStoryGroupResponse
from integrations.storyly.exceptions import StorylyClientBaseException
from settings import CACHE_TTL
from utils.constants import CountryName
from utils.schemas.base import IterPydantic
from utils.schemas.base import PydanticBaseModel


class StoriesInfo(PydanticBaseModel):
    country_name: CountryName
    onboarding_group_id: Optional[str]
    onboarding_token: Optional[str]
    main_token: Optional[str]


class StoriesServiceResponse(IterPydantic[StoriesInfo]):
    pass


async def stories_service() -> StoriesServiceResponse:
    try:
        instances: StorylyInstanceResponse = await storyly_instance()
    except StorylyClientBaseException:
        raise StorylyNotAvailableException
    bulgaria_onboarding_instance: StorylyInstance = next(
        instance for instance in instances.data if instance.title is InstanceTitle.BULGARIA_ONBOARDING
    )
    greece_onboarding_instance: StorylyInstance = next(
        instance for instance in instances.data if instance.title is InstanceTitle.GREECE_ONBOARDING
    )

    key: str = 'storyly_cached_response'
    cached_response: Optional[str] = await Redis.get(key)
    stories_service_response: StoriesServiceResponse
    if cached_response:
        stories_service_response = StoriesServiceResponse.parse_raw(cached_response)
        return stories_service_response

    bulgaria_story_group: StorylyStoryGroupResponse
    greece_story_group: StorylyStoryGroupResponse
    try:
        bulgaria_story_group, greece_story_group = await asyncio.gather(
            storyly_story_group(instance_id=bulgaria_onboarding_instance.id),
            storyly_story_group(instance_id=greece_onboarding_instance.id),
        )
    except StorylyClientBaseException:
        raise StorylyNotAvailableException

    stories_service_response = StoriesServiceResponse(
        __root__=[
            StoriesInfo(
                country_name=CountryName.BULGARIA,
                onboarding_group_id=next(
                    (str(story.id) for story in bulgaria_story_group.data if story.status == 1), None
                ),
                onboarding_token=bulgaria_onboarding_instance.token,
                main_token=next(
                    (instance.token for instance in instances.data if instance.title is InstanceTitle.BULGARIA_MAIN),
                    None,
                ),
            ),
            StoriesInfo(
                country_name=CountryName.GREECE,
                onboarding_group_id=next(
                    (str(story.id) for story in greece_story_group.data if story.status == 1), None
                ),
                onboarding_token=greece_onboarding_instance.token,
                main_token=next(
                    (instance.token for instance in instances.data if instance.title is InstanceTitle.GREECE_MAIN),
                    None,
                ),
            ),
        ]
    )
    await Redis.setex(
        key=key,
        timeout=CACHE_TTL['storyly'],
        value=stories_service_response.json(),
    )
    return stories_service_response
