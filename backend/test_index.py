import motor.motor_asyncio
import asyncio
import os
from dotenv import load_dotenv

async def main():
    load_dotenv('.env')
    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ['MONGO_URL'])
    db = client[os.environ['DB_NAME']]
    print(await db.registrations.index_information())
    
    # Try removing the index if one exists
    indexes = await db.registrations.index_information()
    if 'email_1' in indexes:
        await db.registrations.drop_index('email_1')
        print("Dropped email_1 index")

if __name__ == '__main__':
    asyncio.run(main())
