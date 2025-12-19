# HDMSOFT

[Visit our page](https://odoo.hdmsoft.com.ar)

[Official Documentation](https://rkd-docs.readthedocs.io/en/latest/)

## RKD as ROCKETDOO

Odoo Development Framework

**Made with passion, not just programming skills.**

## Developed by:

   - "Horacio Montaño" and "Elias Braceras"

## Version: 
   - "2.2.0"

----------------------------------------------------------------------------------------------------------------------------------------------------------

### Simple Description:

  RKD, also known as ROCKETDOO, is version 2 of the framework designed for assisted and automated deployment of development environments.
   In this version, unlike its predecessor, it no longer depends on a repository. In other words, it is no longer necessary to create a repository from a template — the framework is now fully independent.

   Developers simply need to install the framework on their local machines using either the pip or pipx package managers. The latter is the recommended option, as it allows installing the framework globally on the developer’s system without dealing with Ubuntu and Debian security restrictions that prevent the direct use of pip install.
   To achieve this, we provide the following two installation options:

   > Ensure you have pip installed, or pipx if necessary.

   ``` 
    pip install rocketdoo==2.0.2.b5 --break-system-packages

   ```
   or 

   ``` 
    pipx install rocketdoo==2.0.2.b5

   ```

  - This development environment is intended for those developing on Linux operating systems, such as Ubuntu, Debian, etc. However, for those who prefer to develop on Windows, we suggest installing **WSL2**, the Windows Subsystem for Linux.
  
  - To use this framework, it is essential to have Docker, Docker Compose, and Git installed.

  - ROCKETDOO version 2 now includes its own execution commands, meaning it is no longer necessary to remember or manually use Docker or Docker Compose commands, as the         framework effectively replaces the most essential ones.

  - Starting from this version, there is no need to use the previous repository as a template to build your environment. Simply by installing the framework, you can create your own directories where the Odoo development environment will be applied.

  - In version 2, you can use either the command rocketdoo or its alias rkd for greater convenience and agility.

  - Gitman is used for downloading and installing external repositories.
  
  - Make sure you have **Docker** and **Docker Compose** installed on your computer. If not, you can follow the official Docker installation guide, [Docker Installation Guide](https://docs.docker.com/engine/install/ubuntu/).


------------------------------------------------------------------------------------------------------------------------------------------------------------

  ### Comand Line

Below we will list the commands that make up the new version of **rocketdoo**

```
 rocketdoo --version

```


```
 rocketdoo --help

```


```
 rocketdoo scaffold

```


```
 rocketdoo init

```


```
 rocketdoo info

```


```
 rocketdoo up

```


```
 rocketdoo up -d

```


```
 rocketdoo status

```


```
 rocketdoo logs

```


```
 rocketdoo stop

```


```
 rocketdoo pause

```


```
 rocketdoo down

```


```
 rocketdoo down -v

```


```
 rocketdoo build

```

  
 
------------------------------------------------------------------------------------------------------------------------------------------------------------

### INSTRUCTIONS:

 1. From your Linux or WSL2 terminal, install the framework using one of the following commands:

 ``` 
    pip install rocketdoo --break-system-packages

   ```
   or 

   ``` 
    pipx install rocketdoo

   ```
   > Make sure you already have pip or pipx installed.
 
 2. Once the framework is installed, navigate to your preferred working directory and create a new directory for your development environment.
 
 3. Inside your development directory, you can start by running the scaffold command.
This command will automatically create all the necessary files and directories required to deploy your Odoo development environment. 
    

 4. Next, you can launch the setup wizard using the init command.
This command will prompt you for all the necessary information to configure your environment.
The questions include:

- Project name

- Odoo version (a list from version 15 to 19 is available for selection)

- Odoo edition (options include Community and Enterprise)

- Whether to use private repositories (for your own developments; it will list your SSH keys connected to your repositories)

- Whether to use third-party repositories (if you answer YES, it will prompt you for the URLs of each repository or repository package)

- PostgreSQL version (selectable option)

- Odoo Master Password (for database creation)

- Container restart policies (selectable option)

- Odoo port (with port validation)

- Visual Studio Code debug port (with port validation)

Port validation checks whether the selected ports are already in use by another instance or service.
If so, it suggests alternative ports or allows you to set them manually
 
 5. Once the setup wizard is completed, you can start the deployment with:

 ```
 rocketdoo up

```
o

```
 rocketdoo up -d

```
The -d flag runs the deployment in detached mode.

 6. After the environment has been successfully deployed, you can access Odoo from your preferred web browser using:
 
 - http://localhost:8069 
 
 or the port you selected during setup.

 7. You can check all environment details using:

 ```
 rocketdoo info

```
This command displays detailed information about the current environment in your working directory.

 8. From this point on, you can begin developing with Visual Studio Code by opening the environment’s directory in your editor.

 9. A partir de este momento ya puedes comenzar a desarrollar con Visual Studio Code, abriendo en el editor de codigo, el directorio de este ambiente! 

> Remember that the addons folder is intended for your own developments, but you can also place standalone modules there if needed.

 11. You can also use the available ROCKETDOO commands to stop, pause, remove, or clean up the environment’s containers and volumes, or to view logs.
 
> In this version of ROCKETDOO, you can use either the command rocketdoo or its alias rkd.

 ------------------------------------------------------------------------------------------------------------------------------------------------------

### Suggestions and Considerations:

 - We recommend using Visual Studio Code Extensions, such as **Docker**, **Dev Container**, and any others you find useful for working in VSCode.

 - If you are developing on **Windows** with **WSL2**, it is recommended to use the **WSL:Ubuntu** extension in **Visual Studio Code** for optimal integration and performance.

 - If you wish to develop in the Enterprise edition, Rocketdoo will ask you, and will make the necessary configurations. However, you should ensure that you have the "enterprise" folder with all the modules and place it in the root of this project.

 - This development framework allows you to use private repositories for your developments. For this, the system will ask you if you want to use “private repositories” and if your answer is YES, it will map your local user folder “~/.ssh/” and ask you to choose which ssh key to use. 
Don't worry, these private keys are not saved in your repository after the commit and push; it simply stores them locally and inside the development docker container. 
Remember that your selected key must be previously configured with your GitHub repository.
This private information is as ephemeral as your environment.

### HOW TO ADD MORE MODULES TO GITMAN IF YOU DID NOT DO IT WITH THE LAUNCHER?

  - If you need to use third-party addons after setting up your development environment with our launcher, you’ll need to manually edit the **gitman.yml** file and also add lines to the **odoo.conf** addons_path using:

      ```sudo nano gitman.yml``` 
  
  and complete each line, starting with the repository URL and version, according to your development deployment version. You’ll need to replicate the set of lines to add more third-party repositories.
  
  If you need help, you can refer to the [gitman official guide](https://gitman.readthedocs.io/en/latest/).

  - In this example, you can see how to add your new module package paths.
  
  Example:
      ```addons_path: usr/lib/python/dist-packages/odoo/extra_addons/,usr/lib/python/dist-packages/odoo/external_addons/account-financial-tools```
    
  - Gitman creates a folder containing all declared modules under **external_addons**, which can be found inside your Odoo web container at the path declared in **odoo.conf**.

  - All paths should be separated by a comma ","


------------------------------------------------------------------------------------------------------------------------------------------------------

### Technical Support

- If you have any questions or issues with our development environment, you can contact us and submit your inquiry or support ticket by clicking on the link below.

 - [Support Link](https://odoo.hdmsoft.com.ar/mesa-de-ayuda)

- If you like this project, and you want to collaborate with a donation, you can do it here !!!

 - [Cafecito](https://cafecito.app/horacio1986)
 - [Patreon](https://cafecito.app/horacio1986)

