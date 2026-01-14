pipeline {
    agent { label 'vmc2' }

    environment {
        OS_CLOUD = 'mycloud'
        TELEGRAM_TOKEN = credentials('TELEGRAM_TOKEN')
        // Убедитесь, что у вас есть в Jenkins credentials с таким id (username/password)
        DOCKER_HUB_CRED = 'docker-hub-cred'
    }

    stages {
        stage('Terraform Apply') {
            steps {
                dir('terraform') {
                    sh '''
                        set -e
                        terraform init
                        terraform apply -auto-approve \
                          -var="image_name=ubuntu-20.04" \
                          -var="flavor_name=m1.medium" \
                          -var="network_name=sutdents-net" \
                          -var="keypair=jenkins-key"
                        terraform output -raw vm_ip > ../ansible/ip.txt
                    '''
                }
            }
        }

        stage('Wait for SSH') {
            steps {
                dir('ansible') {
                    sh '''
                        set -e
                        VM_IP=$(cat ip.txt)
                        echo "Waiting for SSH on ${VM_IP}..."
                        for i in {1..30}; do
                          if nc -z ${VM_IP} 22; then
                            echo "SSH is ready"
                            exit 0
                          fi
                          sleep 5
                        done
                        echo "SSH did not become available"
                        exit 1
                    '''
                }
            }
        }

        stage('Install Docker on VM') {
            steps {
                dir('ansible') {
                    sh '''
                        set -e
                        VM_IP=$(cat ip.txt)
                        echo "Installing Docker and docker-compose on ${VM_IP}..."

                        ssh -o StrictHostKeyChecking=no -i ~/.ssh/jenkins_deploy_rsa ubuntu@${VM_IP} bash -s <<'EOF'
set -e
# Update and prerequisites
sudo apt-get update
sudo apt-get install -y apt-transport-https ca-certificates curl gnupg lsb-release python3-pip software-properties-common

# Add Docker GPG key and repo
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo apt-key add -
sudo add-apt-repository "deb [arch=amd64] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable"

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io

# Add ubuntu user to docker group (so that docker can be used without sudo)
sudo usermod -aG docker ubuntu || true

# Install classic docker-compose binary (fallback)
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose || true
sudo chmod +x /usr/local/bin/docker-compose || true

# Ensure pip and python docker sdk (for community.docker)
sudo pip3 install --upgrade pip
sudo pip3 install docker || true

# Show versions
echo "Docker version:"
sudo docker --version || true
if /usr/local/bin/docker-compose --version 2>/dev/null; then
  /usr/local/bin/docker-compose --version
else
  echo "docker-compose (classic) not found; maybe use 'docker compose' plugin"
fi
EOF
                    '''
                }
            }
        }

        stage('Docker Login') {
            steps {
                dir('ansible') {
                    withCredentials([usernamePassword(credentialsId: env.DOCKER_HUB_CRED, usernameVariable: 'DOCKER_USER', passwordVariable: 'DOCKER_PASS')]) {
                        sh '''
                            set -e
                            VM_IP=$(cat ip.txt)
                            echo "Logging into Docker registry on ${VM_IP}..."
                            # Используем sudo docker login на удалённой машине
                            ssh -o StrictHostKeyChecking=no -i ~/.ssh/jenkins_deploy_rsa ubuntu@${VM_IP} "echo \$DOCKER_PASS | sudo docker login --username \$DOCKER_USER --password-stdin"
                        '''
                    }
                }
            }
        }

        stage('Deploy with Ansible') {
            steps {
                dir('ansible') {
                    sh '''
                        set -e
                        VM_IP=$(cat ip.txt)
                        echo "[servers]" > inventory.ini
                        echo "vm ansible_host=${VM_IP} ansible_user=ubuntu ansible_ssh_private_key_file=~/.ssh/jenkins_deploy_rsa" >> inventory.ini
                        ansible-playbook \
                          -i inventory.ini \
                          --ssh-common-args='-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null' \
                          -e "telegram_token=${TELEGRAM_TOKEN}" \
                          playbook.yml
                    '''
                }
            }
        }

        stage('Verify') {
            steps {
                echo '✓ Deployment successful!'
            }
        }
    }

    post {
        failure {
            echo 'Build failed — проверьте логи.'
        }
    }
}
