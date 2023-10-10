from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.operators.python_operator import PythonOperator
from airflow.providers.postgres.operators.postgres import PostgresOperator
from airflow.sensors.sql_sensor import SqlSensor # Validar los queries
from airflow.utils.task_group import TaskGroup # Agrupar tareas, definir capas

from datetime import timedelta, datetime
from pandas_profiling import ProfileReport
import pytz # Obtener la zona horaria
import pandas as pd

TZ = pytz.timezone('America/Bogota')
TODAY = datetime.now(TZ).strftime('%Y-%m-%d') # Ejecución diaria del script
URL = 'https://raw.githubusercontent.com/ronnygang/webinar/main/titanic.csv'
PATH = '/opt/airflow/dags/data/titanic.csv'
OUTPUT_DQ = '/opt/airflow/dags/data/data_quality_reporte_{}.html'.format(TODAY)
OUTPUT = '/opt/airflow/dags/data/titanic_curated_{}.csv'.format(TODAY)
OUTPUT_SQL = '/opt/airflow/dags/sql/titanic_{}.sql'.format(TODAY)
TARGET = 'sql/titanic_{}.sql'.format(TODAY)

DEFAULT_ARGS = {
    'owner': 'Jhon',
    'retries': 2,
    'retry_delay': timedelta(minutes=0.5),
}

def _profile():
    df = pd.read_csv(PATH)
    profile = ProfileReport(df, title='Data Quality Report')
    profile.to_file(OUTPUT_DQ)

def _curated():
    df = pd.read_csv(PATH)

    # Eliminamos columnas no necesarias
    df.drop(['Ticket', 'Cabin'], axis=1, inplace=True)

    # Rellenamos valores faltantes
    df['Age'].fillna(df['Age'].median(), inplace=True)
    df['Embarked'].fillna(df['Embarked'].mode()[0], inplace=True)

    # Modificar la columna 'Name' para obtener el nombre y el apellido
    df['Full Name'] = df['Name'].apply(lambda x: ' '.join(x.split(',')[1].split('(')[0].strip().split(' ')[1:]))
    df['Full Name'] = df['Full Name'].str.replace('["\']', '', regex=True)

    # Agregar una nueva columna 'Title' a partir de la columna 'Name'
    df['Title'] = df['Name'].apply(lambda x: x.split(',')[1].split('.')[0].strip())

    # Eliminar la columna 'Name'
    df.drop(['Name'], axis=1, inplace=True)

    # Simplificar los titulos
    title_dict = {
        'Capt': 'Officer',
        'Col': 'Officer',
        'Major': 'Officer',
        'Jonkheer': 'Royalty',
        'Don': 'Royalty',
        'Sir': 'Royalty',
        'Dr': 'Officer',
        'Rev': 'Officer',
        'the Countess': 'Royalty',
        'Mme': 'Mrs',
        'Mlle': 'Miss',
        'Ms': 'Mrs',
        'Mr': 'Mr',
        'Mrs': 'Mrs',
        'Miss': 'Miss',
        'Master': 'Master',
        'Lady': 'Royalty'
    }
    df['Title'] = df['Title'].map(title_dict)

    # Reordenar las columnas en el orden deseado
    df = df[['PassengerId', 'Full Name', 'Title', 'Survived', 'Pclass', 'Sex', 'Age', 
            'SibSp', 'Parch', 'Fare', 'Embarked']]
    
    # Guardar el archivo procesado en formato CSV
    df.to_csv(OUTPUT, index=False)

    # Iterar sobre las filas y crear los inserts
    with open(OUTPUT_SQL, 'w') as f:
        for index, row in df.iterrows():
            values = f"({row['PassengerId']}, '{row['Full Name']}', '{row['Title']}', {row['Survived']}, {row['Pclass']}, '{row['Sex']}', {row['Age']}, {row['SibSp']}, {row['Parch']}, {row['Fare']}, '{row['Embarked']}')"
            insert = f"INSERT INTO raw_titanic VALUES {values};\n"
            f.write(insert)

with DAG(
    'dag_webinar',
    description = 'Ejecucion de un data pipeline',
    default_args = DEFAULT_ARGS,
    catchup = False, # Se hará una sola ejecución
    start_date = datetime(2023,1,1),
    schedule_interval = '@once',
    tags = ['webinar', 'sdc'] # Buscar el DAG más fácilmente
) as dag:
    
    descargar = BashOperator(
        task_id = 'descargar_csv',
        bash_command = 'curl -k -o {{ params.path }} {{ params.url }}',
        params = {
            'path': PATH,
            'url': URL
        }
    )

    with TaskGroup('DataQuality', tooltip='Motor de Calidad de Datos') as dq:
        # Hacer un perfilamiento de los datos (reporte)
        profiling = PythonOperator(
            task_id = 'profiling',
            python_callable = _profile
        )

        # Limpieza de datos 
        curated = PythonOperator(
            task_id = 'curated',
            python_callable = _curated
        )

        profiling >> curated

    with TaskGroup('RawLayer', tooltip='Capa Raw') as raw_layer:
        create_raw = PostgresOperator(
            task_id = 'create_raw',
            postgres_conn_id = 'postgres_docker',
            sql = """
                CREATE TABLE IF NOT EXISTS raw_titanic (
                        passenger_id VARCHAR(50),
                        full_name VARCHAR(50),
                        title VARCHAR(50),
                        survived INT,
                        pclass INT,
                        sex VARCHAR(10),
                        age FLOAT,
                        sibsp INT,
                        parch INT,
                        fare FLOAT,
                        embarked VARCHAR(10)
                )
            """
        )

        load_raw = PostgresOperator(
            task_id = 'load_raw',
            postgres_conn_id = 'postgres_docker',
            sql = TARGET
        )

        create_raw >> load_raw

    with TaskGroup('MasterLayer', tooltip='Capa Master') as master_layer:
        create_master = PostgresOperator(
            task_id = 'create_master',
            postgres_conn_id = 'postgres_docker',
            sql = """
                CREATE TABLE IF NOT EXISTS master_titanic (
                        id VARCHAR(50),
                        full_name VARCHAR(50),
                        title VARCHAR(10),
                        survived VARCHAR(50),
                        age VARCHAR(10),
                        generacion VARCHAR(20),
                        genero VARCHAR(10),
                        herm_cony VARCHAR(10),
                        padr_hij VARCHAR(10),
                        prec_bol VARCHAR(10),
                        pto_e VARCHAR(20),
                        load_date VARCHAR(10),
                        load_datetime TIMESTAMP
                )
            """
        )

        carga_master = PostgresOperator(
            task_id = 'carga_master',
            postgres_conn_id = 'postgres_docker',
            sql = """
            INSERT INTO master_titanic (
                id, full_name, title, survived, age, generacion,
                genero, herm_cony, padr_hij, prec_bol, pto_e, load_date, load_datetime
            )
            SELECT DISTINCT
                CAST(passenger_id AS VARCHAR) AS id,
                CAST(full_name AS VARCHAR) AS full_name,
                CAST(title AS VARCHAR) AS title,
                CASE    
                    WHEN survived = 1 THEN 'SOBREVIVIENTE'
                    ELSE 'NO SOBREVIENTE'
                END AS survived,
                CAST(age AS VARCHAR) AS age,
                CASE
                    WHEN age < 18 THEN 'Generación Z'
                    WHEN age BETWEEN 18 AND 39 THEN 'Millenials'
                    WHEN age BETWEEN 40 AND 59 THEN 'Generación X'
                    WHEN age BETWEEN 60 AND 79 THEN 'Baby Boomers'
                    ELSE 'Mayores de 80 años'
                END AS generacion,
                CASE 
                    WHEN sex = 'male' THEN 'MASCULINO'
                    ELSE 'FEMENINO'
                END AS genero,
                CAST(sibsp AS VARCHAR) AS herm_cony,
                CAST(parch AS VARCHAR) AS padr_hij,
                CAST(fare AS VARCHAR) AS prec_bol,
                CASE 
                    WHEN embarked = 'C' THEN 'Cherbourg'
                    WHEN embarked = 'Q' THEN 'Queenstown'
                    WHEN embarked = 'S' THEN 'Southampton'
                END AS pto_e,
                CAST({{ params.today }} AS VARCHAR) AS load_date,
                now() AS load_datetime
            FROM public.raw_titanic t
            -- Siempre y cuando el id aún no exista en la tabla
            WHERE NOT EXISTS (
                SELECT 1 FROM master_titanic lt
                WHERE lt.id = CAST(t.passenger_id AS VARCHAR)
            )
            """,
            params = {'today': TODAY}
        )

        create_master >> carga_master

    # Validar si hoy se han cargado registros 
    validador = SqlSensor(
        task_id = 'validador',
        conn_id = 'postgres_docker',
        sql = 'SELECT COUNT(*) FROM public.master_titanic WHERE load_date = CAST({{ params.today }} AS VARCHAR)',
        params = {
            'today': TODAY
        },
        timeout = 30
    )

    descargar >> dq >> raw_layer >> master_layer >> validador