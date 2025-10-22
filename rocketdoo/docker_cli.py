import click
import subprocess
import shutil
import sys

def ensure_docker_installed():
    """Verify that Docker is installed before running commands."""
    if not shutil.which("docker"):
        click.echo("❌ Docker is not installed or not in the PATH.")
        sys.exit(1)


@click.group()
def docker():
    """Docker commands replaced by Rocketdoo."""
    pass


# ==============================
# 🔼 UP
# ==============================
@docker.command()
@click.option('-d', '--detached', is_flag=True, help="Run containers in detached mode")
@click.argument('extra_args', nargs=-1)
def up(detached, extra_args):
    """Equivalent to: docker compose up"""
    ensure_docker_installed()
    cmd = ["docker", "compose", "up"]
    if detached:
        cmd.append("-d")
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd)
    
# ==============================
# 🔄 RESTART
# ==============================
@docker.command()
@click.option('-t', '--timeout', type=int, help="Timeout in seconds to wait for stop before killing")
@click.argument('services', nargs=-1)
def restart(timeout, services):
    """Equivalent to: docker compose restart"""
    ensure_docker_installed()
    cmd = ["docker", "compose", "restart"]
    if timeout:
        cmd.extend(["-t", str(timeout)])
    if services:
        cmd.extend(services)
    subprocess.run(cmd)


# ==============================
# 🔽 DOWN
# ==============================
@docker.command()
@click.option('-v', '--volumes', is_flag=True, help="Remove volumes when stopping containers")
@click.argument('extra_args', nargs=-1)
def down(volumes, extra_args):
    """Equivalent to: docker compose down"""
    ensure_docker_installed()
    cmd = ["docker", "compose", "down"]
    if volumes:
        cmd.append("-v")
    if extra_args:
        cmd.extend(extra_args)
    subprocess.run(cmd)


# ==============================
# 📋 STATUS
# ==============================
@docker.command(name="status")
def status():
    """Equivalent to: docker compose ps"""
    ensure_docker_installed()
    subprocess.run(["docker", "compose", "ps"])


# ==============================
# ⏹️ STOP
# ==============================
@docker.command()
def stop():
    """Equivalent to: docker compose stop"""
    ensure_docker_installed()
    subprocess.run(["docker", "compose", "stop"])


# ==============================
# ⏸️ PAUSE
# ==============================
@docker.command()
def pause():
    """Equivalent to: docker compose pause"""
    ensure_docker_installed()
    subprocess.run(["docker", "compose", "pause"])


# ==============================
# 🪵 LOGS
# ==============================
@docker.command()
@click.argument("container", required=False)
@click.option('-f', '--follow', is_flag=True, help="Follow logs in real-time")
def logs(container, follow):
    """Equivalent to: docker logs -f <container>"""
    ensure_docker_installed()
    cmd = ["docker", "logs"]
    if follow:
        cmd.append("-f")
    if container:
        cmd.append(container)
    else:
        click.echo("⚠️ You must specify the container name or ID.")
        return
    subprocess.run(cmd)

    
# ==============================
# 🔨 BUILD
# ==============================
@click.command()
@click.option('-t', '--tag', required=False, help="Name of the image to be created (optional)")
@click.option('--rebuild', is_flag=True, help="Rebuild and restart containers (docker compose up -d --build)")
def build(tag, rebuild):
    """
    Builds the Docker image for Rocketdoo.
    
    Examples:
    
    \b
    # Standard build
    rkd build
    
    \b
    # Build with custom tag
    rkd build -t my-image:latest
    
    \b
    # Rebuild and restart all containers
    rkd build --rebuild
    """
    ensure_docker_installed()
    
    if rebuild:
        # Execute docker compose up -d --build
        command = ["docker", "compose", "up", "-d", "--build"]
        click.echo(f"🔄 Rebuilding and restarting containers...")
        click.echo(f"🚀 Executing: {' '.join(command)}")
        subprocess.run(command, check=True)
    else:
        # Standard docker build
        command = ["docker", "build"]
        
        if tag:
            command.extend(["-t", tag])
        
        command.append(".")
        
        click.echo(f"🔨 Building Docker image...")
        click.echo(f"🚀 Executing: {' '.join(command)}")
        subprocess.run(command, check=True)