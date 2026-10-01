from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_session_factory(url: str):
    args = {'check_same_thread': False} if url.startswith('sqlite:') else {}
    engine = create_engine(url, pool_pre_ping=True, connect_args=args)
    return engine, sessionmaker(engine, expire_on_commit=False)
