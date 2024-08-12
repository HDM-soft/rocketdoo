# HDMsoft
## Odoo Skeleton
Repositorio base para desarrollos de Odoo version 17.0

La descripcion del repositorio debe ser modificada para brindar mayor claridad en su uso.

### Descripcion simple:
  Este repositorio debe ser utilizado como molde para cualquier desarrollo de Odoo. El repositorio esta equipado con una seria de herramientas para agilizar el trabajo durante la preparación para el desarrollo.

  - Se utiliza docker para la creacion de instancias de Odoo y docker compose para levantarlas junto con una base de datos.

  - Dispone de una serie de configuraciones preparadas para su uso en VScode como configuraciones para depuracion y lanzamiento de contenedores.

  - Utiliza Gitman para la descarga en instalaciones de repositorios externos.

---

### Como utilizar el repositorio:
 1. En el archivo docker compose modificar el nombre de los contenedores a gusto.
 2. Si se modifico el nombre del contenedor de la base de datos, modificar en el archivo "config/odoo.conf" el campo "db_host" indicando el nuevo nombre del contenedor.
 3. Incluir cualquier configuraciones personalizada en el archivo "config/odoo.conf"
 4. En el archivo "gitman.yml" incluir los enlaces a repositorios externos requeridos por el desarrollo. La herramienta se encargara de descargar el addon en el directorio especificado en el campo "location" e instalar las dependencias de python si existieran.
    1. En el caso de que ocurra un error instalando las dependencias el desarrollador debera entrar al contenedor manualmente e instalarlas o modificar el dockerfile para que se instalen automaticamente.
 5. Incluir en el archivo "config/odoo.conf" los path correspondientes a los addons externos, por ejemplo, utilizando la configuracion ya incluida en el archivo gitman la ruta que debe ser añadida seria: `/usr/lib/python3/dist-packages/odoo/external_addons/oca_maintenance`
    1. El path esta compuesto por el path donde se instala odoo dentro del contenedor seguido por "location" definida en el "gitman.yml" y finalmente "name" definido en el gitman.yml para ese "source".
 6. Ejecutar el comando `docker build . -t odoo-skeleton`
    1. El parametro -t es para darle un nombre y un tag a la imagen, puede reemplazarse por cualquier valos, pero en el caso de que lo reemplace debe modificar el campo "image" del docker compose indicando el nombre que le designo a la imagen.
 7. Si la imagen se construye con exito puede ejecutar el comando `docker compose up -d` para levantar los contenedores o levantarlos directamente desde VScode
