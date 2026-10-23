-- Roles de PostgreSQL con privilegios mínimos para cuentas.
--
--   cuentas_owner  dueño de las tablas; solo para "manage.py migrate" y las copias de seguridad
--   cuentas_app    el que usa la aplicación a diario: leer y escribir datos, sin cambiar el esquema
--
-- Ejecutar como superusuario (postgres) sobre la base de datos de la app, con contraseñas largas:
--
--   psql -U postgres -d cuentas -v owner_pass='...' -v app_pass='...' -f deploy/postgres/roles.sql
--
-- Es idempotente: se puede volver a ejecutar (p. ej. para cambiar las contraseñas).
-- Después, en el .env:  DB_USER=cuentas_app  DB_PASSWORD=<app_pass>
-- y para migrar:        DB_USER=cuentas_owner DB_PASSWORD=<owner_pass> python manage.py migrate
-- (ver docs/security.md §5)

\set ON_ERROR_STOP on

SELECT format('CREATE ROLE cuentas_owner LOGIN PASSWORD %L', :'owner_pass')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cuentas_owner') \gexec
SELECT format('CREATE ROLE cuentas_app LOGIN PASSWORD %L', :'app_pass')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'cuentas_app') \gexec

ALTER ROLE cuentas_owner WITH LOGIN NOSUPERUSER NOCREATEROLE NOREPLICATION CREATEDB PASSWORD :'owner_pass';
ALTER ROLE cuentas_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_pass';

-- Nadie más que estos roles se conecta a la base de datos
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO cuentas_owner, cuentas_app', current_database()) \gexec

-- El esquema public pasa a cuentas_owner; cuentas_app solo puede usarlo
ALTER SCHEMA public OWNER TO cuentas_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cuentas_app;

-- Las tablas, secuencias y vistas existentes (creadas hasta ahora por postgres) pasan a cuentas_owner
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT c.relname, c.relkind
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
              AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = c.oid AND d.deptype IN ('a', 'i'))
  LOOP
    EXECUTE format('ALTER %s public.%I OWNER TO cuentas_owner',
                   CASE r.relkind WHEN 'S' THEN 'SEQUENCE' WHEN 'v' THEN 'VIEW'
                                  WHEN 'm' THEN 'MATERIALIZED VIEW' WHEN 'f' THEN 'FOREIGN TABLE'
                                  ELSE 'TABLE' END,
                   r.relname);
  END LOOP;
END $$;

-- cuentas_app: datos sí, esquema no (ni DROP, ni ALTER, ni TRUNCATE)
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO cuentas_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO cuentas_app;

-- Y lo mismo para lo que cree cuentas_owner en futuras migraciones
ALTER DEFAULT PRIVILEGES FOR ROLE cuentas_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO cuentas_app;
ALTER DEFAULT PRIVILEGES FOR ROLE cuentas_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO cuentas_app;
