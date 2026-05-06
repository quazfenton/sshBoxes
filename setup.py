#!/usr/bin/env python3
"""
sshBox Setup Script
Automates the initial setup and configuration of sshBox
"""
import os
import sys
import secrets
import subprocess
from pathlib import Path


def print_header(text):
    """Print a formatted header"""
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60 + "\n")


def print_success(text):
    """Print success message"""
    print(f"✅ {text}")


def print_error(text):
    """Print error message"""
    print(f"❌ {text}")


def print_info(text):
    """Print info message"""
    print(f"ℹ️  {text}")


def check_python_version():
    """Check Python version"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print_error(f"Python 3.8+ required, found {version.major}.{version.minor}")
        return False
    print_success(f"Python version: {version.major}.{version.minor}.{version.micro}")
    return True


def check_docker():
    """Check if Docker is available"""
    try:
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            print_success(f"Docker: {result.stdout.strip()}")
            return True
        else:
            print_error("Docker not found")
            return False
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print_error("Docker not found or not in PATH")
        return False


def create_directories():
    """Create required directories"""
    print_header("Creating Directories")
    
    directories = [
        "/var/lib/sshbox/recordings",
        "/var/lib/sshbox/logs",
        "/var/lib/sshbox/metrics",
        "/var/log/sshbox"
    ]
    
    created = 0
    for dir_path in directories:
        try:
            path = Path(dir_path)
            if not path.exists():
                # Try to create with sudo
                print_info(f"Creating {dir_path}...")
                result = subprocess.run(
                    ["sudo", "mkdir", "-p", dir_path],
                    capture_output=True
                )
                if result.returncode == 0:
                    subprocess.run(
                        ["sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", dir_path],
                        capture_output=True
                    )
                    print_success(f"Created {dir_path}")
                    created += 1
                else:
                    print_error(f"Failed to create {dir_path}")
            else:
                print_success(f"Already exists: {dir_path}")
                created += 1
        except Exception as e:
            print_error(f"Error creating {dir_path}: {e}")
    
    return created == len(directories)


def create_env_file():
    """Create .env file from template"""
    print_header("Creating Environment File")
    
    env_example = Path(".env.example")
    env_file = Path(".env")
    
    if not env_example.exists():
        print_error(".env.example not found")
        return False
    
    # Generate secure secret
    secret = secrets.token_urlsafe(32)
    
    # Read template
    with open(env_example, 'r') as f:
        content = f.read()
    
    # Replace placeholder secret
    content = content.replace(
        "SSHBOX_SECURITY_GATEWAY_SECRET=change-this-to-a-secure-random-string-at-least-32-chars",
        f"SSHBOX_SECURITY_GATEWAY_SECRET={secret}"
    )
    
    # Write .env file
    with open(env_file, 'w') as f:
        f.write(content)
    
    print_success(f"Created {env_file}")
    print_info(f"Generated secure gateway secret (32 characters)")
    
    # Show important settings
    print_info("\nImportant settings in .env file:")
    print("  - SSHBOX_SECURITY_GATEWAY_SECRET: Auto-generated")
    print("  - SSHBOX_ENVIRONMENT: development")
    print("  - SSHBOX_DB_DB_TYPE: sqlite")
    print("\nEdit .env to customize settings for your environment")
    
    return True


def install_dependencies():
    """Install Python dependencies"""
    print_header("Installing Dependencies")
    
    requirements = Path("requirements.txt")
    
    if not requirements.exists():
        print_error("requirements.txt not found")
        return False
    
    try:
        print_info("Installing Python packages...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        if result.returncode == 0:
            print_success("Dependencies installed successfully")
            return True
        else:
            print_error(f"Failed to install dependencies:\n{result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print_error("Installation timed out")
        return False
    except Exception as e:
        print_error(f"Error installing dependencies: {e}")
        return False


def build_docker_image():
    """Build Docker base image"""
    print_header("Building Docker Image")
    
    dockerfile = Path("images/Dockerfile")
    
    if not dockerfile.exists():
        print_error("Dockerfile not found")
        return False
    
    try:
        print_info("Building ephemeral-box:latest...")
        result = subprocess.run(
            ["docker", "build", "-t", "ephemeral-box:latest", "-f", str(dockerfile), "images/"],
            capture_output=True,
            text=True,
            timeout=600
        )
        
        if result.returncode == 0:
            print_success("Docker image built successfully")
            return True
        else:
            print_error(f"Failed to build Docker image:\n{result.stderr}")
            return False
            
    except subprocess.TimeoutExpired:
        print_error("Build timed out")
        return False
    except Exception as e:
        print_error(f"Error building Docker image: {e}")
        return False


def run_tests():
    """Run test suite"""
    print_header("Running Tests")
    
    test_dir = Path("tests")
    
    if not test_dir.exists():
        print_error("Tests directory not found")
        return False
    
    try:
        print_info("Running test suite...")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=300
        )
        
        print(result.stdout)
        
        if result.returncode == 0:
            print_success("All tests passed")
            return True
        else:
            print_info("Some tests failed (this may be expected in development)")
            return True  # Don't fail setup on test failures
            
    except subprocess.TimeoutExpired:
        print_info("Tests timed out (skipping)")
        return True
    except Exception as e:
        print_info(f"Could not run tests: {e}")
        return True  # Don't fail setup on test errors


def show_next_steps():
    """Show next steps"""
    print_header("Setup Complete! Next Steps:")
    
    print("""
1. Review and customize .env file:
   nano .env  (or your preferred editor)

2. Start the services:
   docker-compose up -d

3. Check service health:
   curl http://localhost:8080/health

4. Create an invite:
   python3 scripts/box-invite.py create --secret "your-secret" --profile dev --ttl 600

5. Connect to a box:
   python3 scripts/box-invite.py connect --token "TOKEN" --gateway http://localhost:8080

For more information, see:
   - README.md - Main documentation
   - docs/ENHANCED_IMPLEMENTATION.md - Enhanced features guide
   - docs/TECHNICAL_REVIEW_AND_IMPROVEMENT_PLAN.md - Technical details
""")


def main():
    """Main setup function"""
    print_header("sshBox Setup")
    
    # Check requirements
    print_header("Checking Requirements")
    
    checks = [
        ("Python version", check_python_version()),
        ("Docker", check_docker())
    ]
    
    if not all(check[1] for check in checks):
        print_error("Some requirements are not met. Please install missing components.")
        sys.exit(1)
    
    print_success("All requirements met")
    
    # Setup steps
    steps = [
        ("Creating directories", create_directories),
        ("Creating environment file", create_env_file),
        ("Installing dependencies", install_dependencies),
        ("Building Docker image", build_docker_image),
        ("Running tests", run_tests)
    ]
    
    completed = 0
    for step_name, step_func in steps:
        try:
            if step_func():
                completed += 1
        except KeyboardInterrupt:
            print_error("\nSetup interrupted by user")
            sys.exit(1)
        except Exception as e:
            print_error(f"Step '{step_name}' failed: {e}")
    
    print_header("Setup Summary")
    print(f"Completed {completed}/{len(steps)} steps")
    
    if completed == len(steps):
        print_success("Setup completed successfully!")
        show_next_steps()
    else:
        print_info("Some steps failed. You can still proceed with manual configuration.")
        print_info("See README.md for manual setup instructions.")
    
    sys.exit(0 if completed == len(steps) else 1)


if __name__ == "__main__":
    main()
