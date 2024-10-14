# HDMsoft
[visite nuestra pagina](https://odoo.hdmsoft.com.ar)

## ROCKETDOO

Repositorio base para desarrollos en Odoo.

## Desarrollado por:

   - "Elias Braceras"
   - "Horacio Montaño"

## Version: 
   - "1.0"

----------------------------------------------------------------------------------------------------------------------------------------------------------

### Descripcion simple:

  Este repositorio debe ser utilizado como molde para cualquier desarrollo de Odoo. El repositorio esta equipado con una serie de herramientas para agilizar el trabajo durante la preparación para el desarrollo. Este entorno de desarrollo esta pensado como una libreria de Python, por tanto, es importante instalarla
  como tal.

  - Este entorno de desarrollo esta pensado para quienes desarrollen en sistemas operativos Linux, tales como Ubuntu, Debian, etc. Sin embargo
    para quienes prefieran desarrollar en Windows les sugerimos que instalen en sus máquinas **WSL2** Subsistema de Linux para Windows.
  
  - Usted dispone en esta versión de un lanzador de proyecto CLI, diseñado para facilitar el armado de su proyecto de desarrollo.
  
  - Se utiliza docker para la creación de instancias de Odoo y docker compose para levantarlas junto con una base de datos.

  - Dispone de una serie de configuraciones preparadas para su uso en VScode como configuraciones para depuración y lanzamiento de contenedores.

  - Utiliza Gitman para la descarga e instalación de repositorios externos.
  
  -  Asegúrese de tener instalado **Docker** y **Docker compose** en su computadora, en el caso de no tener instalada dicha herramienta,
 puede servirse de la guia oficial de Docker para instalar, [Guía para la Instalación de Doker](https://docs.docker.com/engine/install/ubuntu/)

  - Este ambiente cuenta con la posibilidad de trabajar con repositorios privados. Para esto, se le solicita al usuario desarrollador que copie su llave
  privada y su par pública, las mismas con la que tiene configurado su repo de github, en la carpeta **.ssh** de este directorio.
 
 
------------------------------------------------------------------------------------------------------------------------------------------------------------

### INSTRUCCIONES:
 

 1. Como primer paso usted debe utilizar el repositorio de HDMSOFT, nuestra plantilla **rocketdoo**, creando un repositorio a partir de la misma con el botón
 **use this template** que se encuentra en la esquina superior derecha en color verde.
 
 2. Determinar el nombre que desea para su repositorio de desarrollo.
 
 3. Una vez creado su repositorio, debe clonar el mismo:
 
    ```git clone "url_de_su_repo"``` 
    

 4. Antes de comenzar con la ejecución del lanzador, ingrese al directorio de su repositorio e instale las dependencias con el comando:
 
    ```sudo pip3 install -r requirements.txt```
 
 5. Ahora puede ejecutar su ROCKETDOO con el comando: 
 
    ```rocketdoo```

 6. Siga los pasos indicados por el software de lanzamiento.

 7. La segunda etapa del LANZADOR le ofrece la opción de usar **GITMAN** para repositorios de terceros, si le indica que sí! Deberá completar las preguntas.

 8. Nuestro lanzador se encargara de modificar el archivo odoo.conf en la linea "addons_path" con los nuevos repositorios.

 9. Una vez finalizado su proyecto, debe ejecutar el siguiente comando para levantar su instancia en local: 
    
    ```docker compose up```

 11. RocketDoo comenzará a construir la imagen de su entorno y seguido de esto, levanta su sistema. 
    Una vez finlizado con éxtio puede verificar ingresando la siguiente URL en si navegador web; **localhost:puerto**
 
 12. Si su proyecto finalizo con éxito, podrá ejecutar el comando:
    
    ```code .```
    
    ¡AHORA PUEDE COMENZAR A DESARROLLAR CON "VISUAL STUDIO CODE"! 

 ------------------------------------------------------------------------------------------------------------------------------------------------------

### Sugerencias y Consideraciones:

 - Recomendamos utilizar Extensiones de Visual Studio Code, como **Docker**, **Dev Container**, y todas aquellas que considere de utilidad
 para poder trabajar en VSCode.

 - Si desea desarrollar en edición Enterprise, Rocketdoo se lo preguntará, y realizará las configuraciones necesarias; pero deberá usted considerar
 disponer de la carpet "enterprise" con todos los modulos y dejarla en la raíz de este proyecto.
 
### ¿COMO CARGAR MAS MODULOS EN GITMAN SI NO LO HICE CON EL LANZADOR?
 
  - Si usted necesita ocupar addons de terceros, luego de haber construido su entorno de desarrollo con nuestro lanzador, deberá editar a mano el archivo **gitman.yml** y a su vez deberá también agregar las lineas al path del **odoo.conf** addons_path con el comando:
  
      ```sudo nano gitman.yml``` 
  
  y completar cada linea, comenzando por la URL del repositorio, la versión
  del mismo, acorde a la versión de su despliegue de desarrollo. Deberá replicar el conjunto de lineas para agregar mas repositorios de terceros.
  Si tiene dudas con el uso, puede revisar la guía oficial de [gitman](https://gitman.readthedocs.io/en/latest/)

  - En este ejemplo puede ver como agregar las rutas de sus nuevos paquetes de módulos.
  Ejemplo:
      ```addons_path: usr/lib/python/dist-packages/odoo/extra_addons/,usr/lib/python/dist-packages/odoo/external_addons/account-financial-tools```
    
  - Gitman crea una carpeta donde contendrá todos los módulos declarados con el nombre de **external_addons** los mismos los puede localizar 
  dentro de su contenedor web Odoo en la ruta declarada en el **odoo.conf**

  - Todos los paths deberán estar separados por una coma ","
  - Una vez declarados los módulos de terceros en "gitman" sera necesario hacer un rebuild de su imagen, respetando el mismo nombre 
  de la imagen creada en un principio, con el comando 

  - **RECUERDE QUE CUANDO INICIE POR PRIMERA VEZ NUESTRO LANZADOR, EL MISMO LO GUIA PARA EVITAR CARGAR LOS MODULOS A MANO EN GITMAN**

      ```docker build . -t nombre-de-mi-imagen```
  
  - Luego reiniciar el serivcio Odoo o el contenedor con el comando 
    
      ```docker compose restart```
  
  y una vez dentro de su instancia Odoo
  actualizar la lista de aplicaciones en modo "desarrollador" para poder tener los módulos nuevos visibles. 

Si usted modifica su archivo **gitman.yml** antes de realizar la construcción con el comando "build" sera mas que suficiente para poder ver los módulos
sin necesidad de reiniciar sus contenedores.

  - Todos los módulos de terceros simples, como también el modulo que este por desarrollar, aconsejamos que los coloque en la carpeta **addons** que se encuentra dentro del directorio de trabajo.


------------------------------------------------------------------------------------------------------------------------------------------------------

### Soporte Técnico

- Si usted tiene alguna duda o inconveniente con el funcionamiento de nuestro ambiente de desarrollo, puede contactarse y enviar su consulta o ticket
de soporte presionando en el siguiente link.

 - [Link de Soporte](https://odoo.hdmsoft.com.ar/contactus)


