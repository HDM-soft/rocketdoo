# HDMSOFT

[Visit our page](https://odoo.hdmsoft.com.ar)

[Official Documentation](https://rocketdoo-docs.readthedocs.io/en/latest/)

## RKD as ROCKETDOO

Odoo Development Framework

## Developed by:

   - "Horacio Montaño"

## Version: 
   - "2.0.0b1"

----------------------------------------------------------------------------------------------------------------------------------------------------------

### Simple Description:

  RKD, also known as ROCKETDOO, is version 2 of the framework designed for assisted and automated deployment of development environments.
   In this version, unlike its predecessor, it no longer depends on a repository. In other words, it is no longer necessary to create a repository from a template — the framework is now fully independent.

   Developers simply need to install the framework on their local machines using either the pip or pipx package managers. The latter is the recommended option, as it allows installing the framework globally on the developer’s system without dealing with Ubuntu and Debian security restrictions that prevent the direct use of pip install.
   To achieve this, we provide the following two installation options:

   > Asegurese de tener instalado pip o en su defecto pipx

   ``` 
    pip install rocketdoo --break-system-packages

   or

   ``` 
    pipx install rocketdoo

  - This development environment is intended for those developing on Linux operating systems, such as Ubuntu, Debian, etc. However, for those who prefer to develop on Windows, we suggest installing **WSL2**, the Windows Subsystem for Linux.
  
  - To use this framework, it is essential to have Docker, Docker Compose, and Git installed.

  - ROCKETDOO version 2 now includes its own execution commands, meaning it is no longer necessary to remember or manually use Docker or Docker Compose commands, as the         framework effectively replaces the most essential ones.

  - Starting from this version, there is no need to use the previous repository as a template to build your environment. Simply by installing the framework, you can create your own directories where the Odoo development environment will be applied.

  - In version 2, you can use either the command rocketdoo or its alias rkd for greater convenience and agility.

  - Gitman is used for downloading and installing external repositories.
  
  - Make sure you have **Docker** and **Docker Compose** installed on your computer. If not, you can follow the official Docker installation guide, [Docker Installation Guide](https://docs.docker.com/engine/install/ubuntu/).


------------------------------------------------------------------------------------------------------------------------------------------------------------

  ### Comandos

A continuacion listaremos los comandos que componen la nueva version de **rocketdoo**

```
 rocketdoo --version

```
 rocketdoo --help

```
 rocketdoo scaffold

```
 rocketdoo init

```
 rocketdoo info

```
 rocketdoo up

```
 rocketdoo up -d

```
 rocketdoo status

```
 rocketdoo logs

```
 rocketdoo stop

```
 rocketdoo pause

```
 rocketdoo down

```
 rocketdoo down -v

```
 rocketdoo build

  
 
------------------------------------------------------------------------------------------------------------------------------------------------------------

### INSTRUCTIONS:

 1. The first step is to use the HDMSOFT repository, our **rocketdoo** template, by creating a repository from it using the green **use this template** button at the top right corner.
 
 2. Decide on a name for your development repository.
 
 3. Once your repository is created, clone it:

    ```git clone "your_repo_url"``` 
    

 4. Before running the launcher, navigate to your repository directory and install the dependencies using the command:
 
    ```sudo pip3 install -r requirements.txt```
 
 5. To start, you must enter the subdirectory “rocketdoo” located in the root of rocketdoo; and then execute the following command: 
 
    ```rocketdoo```

 6. Follow the steps provided by the launch software.

 7. The second stage of the LAUNCHER offers you the option to use **GITMAN** for third-party repositories. If you say yes, you’ll need to answer some questions.

 8. Our launcher will modify the odoo.conf file in the "addons_path" line with the new repositories.

 9. Once your project is ready, you can bring up your local instance with: 
    
    ```docker compose up```

 11. RocketDoo will start building the environment image and then launch the system. Once successfully completed, you can check by entering the following URL in your web browser: **localhost:port**
 
 12. If your project has launched successfully, you can run the command:
    
    ```code .```
    
    NOW YOU CAN START DEVELOPING WITH "VISUAL STUDIO CODE"! 

 ------------------------------------------------------------------------------------------------------------------------------------------------------

### Suggestions and Considerations:

 - We recommend using Visual Studio Code Extensions, such as **Docker**, **Dev Container**, and any others you find useful for working in VSCode.

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
  - Once third-party modules are declared in "gitman," you’ll need to rebuild your image, keeping the same image name as initially created, using the command:

      ```docker build . -t my-image-name```
  
  - Then restart the Odoo service or container with the command:
    
      ```docker compose restart```
  
  and once in your Odoo instance, refresh the application list in "developer" mode to see the new modules.

If you modify your **gitman.yml** file before building with the "build" command, it will be sufficient to see the modules without needing to restart your containers.

  - For simpler third-party modules, as well as the module you’re developing, we recommend placing them in the **addons** folder within your working directory.

------------------------------------------------------------------------------------------------------------------------------------------------------

### Technical Support

- If you have any questions or issues with our development environment, you can contact us and submit your inquiry or support ticket by clicking on the link below.

 - [Support Link](https://odoo.hdmsoft.com.ar/mesa-de-ayuda)

- If you like this project, and you want to collaborate with a donation, you can do it here !!!

 - [Cafecito](https://cafecito.app/horacio1986)
 - [Patreon](https://cafecito.app/horacio1986)

