import argparse
import getpass
import os
from sqlalchemy import select
from .config import settings
from .db import make_session_factory
from .models import Admin, Bot, Pipeline, Stage
from .security import hash_password


def main():
    parser=argparse.ArgumentParser(description='CRM administration')
    parser.add_argument('command', choices=['create-admin','seed-bots','seed-crm'])
    parser.add_argument('--email', default=os.getenv('ADMIN_EMAIL'))
    args=parser.parse_args()
    engine, factory=make_session_factory(settings().database_url)
    with factory() as db:
        if args.command=='seed-bots':
            for key,name in [('food_porn','Food Porn')]:
                if db.scalar(select(Bot).where(Bot.key==key)) is None:
                    db.add(Bot(key=key,name=name))
            db.commit()
            print('Bot record ready: Food Porn; add other bot keys in the web admin')
            return
        if args.command=='seed-crm':
            name='Разработка веб-проектов'
            row=db.scalar(select(Pipeline).where(Pipeline.name==name))
            if row is None:
                row=Pipeline(name=name,description='Сайты, порталы, веб-приложения и интеграции')
                db.add(row);db.flush()
                stages=[('Новая заявка',10,''),('Квалификация',25,''),('Оценка',45,''),('Предложение',65,''),('Согласование',85,''),('Выиграна',100,'won'),('Проиграна',0,'lost')]
                for pos,(title,prob,terminal) in enumerate(stages,1):
                    db.add(Stage(pipeline_id=row.id,name=title,position=pos,probability=prob,terminal=terminal))
            db.commit()
            print('Web-project sales pipeline ready')
            return
        if not args.email: parser.error('Provide --email or ADMIN_EMAIL')
        password=os.getenv('ADMIN_PASSWORD') or getpass.getpass('Password: ')
        if password.startswith('REPLACE_'): parser.error('Set a unique ADMIN_PASSWORD first')
        email=args.email.strip().lower()
        row=db.scalar(select(Admin).where(Admin.email==email))
        if row is None:
            row=Admin(email=email,password_hash=hash_password(password),role='owner')
            db.add(row)
        else:
            row.password_hash=hash_password(password)
        db.commit()
        print(f'Admin account ready: {email}')
    engine.dispose()


if __name__=='__main__': main()
