from __future__ import annotations

from platform_mediation.config import get_settings
from platform_mediation.repositories.sqlalchemy_models import Base
from platform_mediation.repositories.sqlalchemy_repo import SqlAlchemyPlatformMediationRepository


def main() -> None:
    settings = get_settings()
    repository = SqlAlchemyPlatformMediationRepository.from_database_url(
        settings.database_url,
        auto_create_schema=True,
    )
    # 通过初始化仓储触发建表，这里只输出当前目标，方便本地执行确认。
    print(f"initialized sqlalchemy repository for {settings.database_url}")
    print(f"tables={len(Base.metadata.tables)}")
    _ = repository


if __name__ == "__main__":
    main()

