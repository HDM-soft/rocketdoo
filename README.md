# HDMsoft
## Odoo Skeleton
Repositorio base para desarrollos en Odoo.

## Desarrollado por:
   - "Elias Braceras"
   - "Horacio Montaño"

----------------------------------------------------------------------------------------------------------------------------------------------------------

### Descripcion simple:

  Este repositorio debe ser utilizado como molde para cualquier desarrollo de Odoo. El repositorio esta equipado con una serie de herramientas para agilizar el trabajo durante la preparación para el desarrollo.
  
  - Usted dispone en esta version de un lanzador de proyecto CLI, diseñado para facilitar el armado de su proyecto de desarrollo.
  
  - Se utiliza docker para la creacion de instancias de Odoo y docker compose para levantarlas junto con una base de datos.

  - Dispone de una serie de configuraciones preparadas para su uso en VScode como configuraciones para depuracion y lanzamiento de contenedores.

  - Utiliza Gitman para la descarga e instalacion de repositorios externos.

  - Utilice la rama **main** para los desarrollos en ediciones community.

------------------------------------------------------------------------------------------------------------------------------------------------------------

### Como utilizar el repositorio:
 
 0. Antes de comenzar asegurese de tener instalado **Docker** y **Docker compose** en su computadora, en el caso de no tener instalada dicha herramienta,
 puede servirse de la guia oficial de Docker para instalar, [(https://docs.docker.com/engine/install/ubuntu/)]

 1. El primer paso antes de comenzar usted debera instalar en su maquina la libreria de Python **copier** con el siguiente comando: 
    ```pip3 install copier```
 o 
    ```sudo apt install python3-copier```
 , si usted esta trabajando en un ambiente de Python use el siguiente comando 
    ```pipx install copier```

 2. Como segundo paso usted debe utilizar el repositorio de HDMSOFT, nuestra plantilla **odoo-skeleton**, creando un repositorio a partir de la misma con el boton
 **use this template** que se encuentra en la esquina superior derecha en color verde.
 
 3. Determinar el nombre que desea para su repo de desarrollo, asi como tambien verificar si desea incluir todas las ramas o alguna en especial.
 
 4. Clonar su repositorio una vez creado con el comando:
 
    ```git clone -b "nombre_de_la_rama´ "url_del_repo```
 
 5. Una vez clonado su repo, debe ingresar al directorio del mismo y ejecutar el siguiente comando: 
    ```copier copy "/ruta-absoluta/de/mi/repo /ruta/destino``` 
 es idea que la ruta destino, sea la misma donde se encuentra para eso en ruta destino
use el siguiente comando "./"
esto hara que el proyecto de inicio en el directorio de su repositorio.

 6. Siga los pasos indicados por el software de lanzamiento.

 7. Una vez finalizado su proyecto, debe crear la imagen con el siguiente comando de docker 
    ```docker build . -t nombre-de-mi-imagen```

 8. Ahora podra lanzar su ambiente de desarrollo con el comando 
    ```docker compose up -d```

 9. Puede verificar que su instancia este corriendo con el comando 
    ```docker compose ps```
    
 o dirigiendose a su navegador de preferencia y usando la url **localhost:puerto**
 
 7. Si su proyecto finalizo con exito, podra ejecutar el comando 
    ```code .```
    
 para comenzar a desarrollar en Visual Studio Code.

 ------------------------------------------------------------------------------------------------------------------------------------------------------

 ### Sugerencias y Consideraciones:

 - Recomendamos utilizar Extensiones de Visual Studio Code, como **Docker**, **Dev Conainer**, y todas aquellas que considere de utilidad
 para poder trabajar comodamente en VSCode.
 
  - Si usted necesita ocupar addons de terceros, sobre todo aquellos que son paquetes que contienen un conjunto de modulos como lo son el repositorio
  de OCA "web", o repositorios de ADHOC, como "account-financial-tools", le recomendamos utilizar la herramienta **gitman.yml**
  puede acceder a ella con el comando ´sudo nano gitman.yml´ y completar cada linea, comenzando por la url del repositorio, la version
  del mismo, acorde a la version de su despliegue de desarrollo. Debera replicar el conjunto de lineas para agregar mas repositorios de terceros.
  Si tiene dudas con el uso, puede revisar la guia oficial de [gitman](https://gitman.readthedocs.io/en/latest/)
  - Todos los repositorios declarados en "gitman" deberan ser declarados en el archivo **odoo.conf** en la linea de **addons_path**
  Ejemplo:
    ```addons_path: usr/lib/python/dist-packages/odoo/extra_addons/,usr/lib/python/dist-packages/odoo/external_addons/account-financial-tools```
    
  Siendo la segunda linea declarada, la que corresponde con los paquetes de modulos de ADHOC. 
  - Gitman crea una carpeta donde contendra todos los modulos declarados con el nombre de **external_addons** los mismos los puede localizar 
  dentro de su contenedor web Odoo en la ruta declarada en el **odoo.conf**
  - Todos los paths deberan estar separados por una coma ","
  - Una vez declarados los modulos de terceros en "gitman" sera necesario hacer un rebuild de su imagen, respetando el mismo nombre 
  de la imagen creada en un principio, con el comando 
    ```docker build . -t nombre-de-mi-imagen```
  
  - Luego reiniciar el serivcio Odoo o el contenedor con el comando 
    ```docker compose restart```
  y una vez dentro de su instancia Odoo
  actualizar la lista de aplicaciones en modo "desarrollador" para poder tener los modulos nuevos visibles. 

Si usted modifica su archivo **gitman.yml** antes de realizar la construccion con el comando "build" sera mas que suficiente para poder ver los modulos
sin necesidad de reiniciar sus contenedores.

  - Todos los modulos de terceros simples, aconsejamos que los coloque en la carpeta **addons** que se encuentra dentro del directorio de trabajo.


------------------------------------------------------------------------------------------------------------------------------------------------------

### Soporte Técnico

- Si usted tiene alguna duda o inconveniente con el funcionamiento de nuestro ambiente de desarrollo, puede contactarse y enviar su consuslta o ticket
de soporte presionando en el siguiente link. 

 - [Link de Soporte](https://odoo.hdmsoft.com.ar/contactus)


