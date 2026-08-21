import os


if os.environ.get('PYTHON_DOTENV_DISABLED') == '1':
    os.environ.setdefault('DB_DIALECT', 'postgresql')
    os.environ.setdefault('DB_DRIVER', 'asyncpg')
    os.environ.setdefault('SECRET', 'test-secret')
    os.environ.setdefault('FIRST_SUPERUSER_EMAIL', 'test@example.com')
    os.environ.setdefault('FIRST_SUPERUSER_PASSWORD', 'test-password')
    os.environ.setdefault('POSTGRES_USER', 'test-user')
    os.environ.setdefault('POSTGRES_PASSWORD', 'test-password')
    os.environ.setdefault('POSTGRES_DB', 'test-db')
    os.environ.setdefault('POSTGRES_PORT', '5432')
    os.environ.setdefault('POSTGRES_HOST', 'localhost')
