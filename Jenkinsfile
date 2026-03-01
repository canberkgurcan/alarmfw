// Gerekli Jenkins değişkenleri:
//   REGISTRY_URL     — Nexus registry adresi         (örn: nexus.internal:5000)
//   REGISTRY_CREDS   — Jenkins credential ID         (Docker kullanıcı/şifre)
//   OCP_API_URL      — OpenShift API endpoint        (örn: https://api.cluster.local:6443)
//   OCP_TOKEN_CREDS  — Jenkins credential ID         (OCP service account token)
//   DEPLOY_NAMESPACE — Deploy namespace              (örn: alarmfw-prod)

pipeline {
    agent any

    environment {
        IMAGE_NAME = 'alarmfw'
        FULL_IMAGE = "${REGISTRY_URL}/${IMAGE_NAME}:${BUILD_NUMBER}"
    }

    stages {
        stage('Checkout SCM') {
            steps {
                checkout scm
            }
        }

        stage('Docker Build') {
            steps {
                sh "docker build -t ${FULL_IMAGE} ."
            }
        }

        stage('Nexus Push') {
            steps {
                withCredentials([usernamePassword(
                    credentialsId: "${REGISTRY_CREDS}",
                    usernameVariable: 'REG_USER',
                    passwordVariable: 'REG_PASS'
                )]) {
                    sh """
                        echo \$REG_PASS | docker login ${REGISTRY_URL} -u \$REG_USER --password-stdin
                        docker push ${FULL_IMAGE}
                        docker logout ${REGISTRY_URL}
                    """
                }
            }
        }

        stage('OCP Deploy') {
            steps {
                withCredentials([string(credentialsId: "${OCP_TOKEN_CREDS}", variable: 'OCP_TOKEN')]) {
                    sh """
                        oc login ${OCP_API_URL} --token=\$OCP_TOKEN --insecure-skip-tls-verify=true
                        oc apply -f ocp/deployment.yaml -n ${DEPLOY_NAMESPACE}
                        oc set image deployment/${IMAGE_NAME} ${IMAGE_NAME}=${FULL_IMAGE} -n ${DEPLOY_NAMESPACE}
                        oc rollout status deployment/${IMAGE_NAME} -n ${DEPLOY_NAMESPACE} --timeout=120s
                    """
                }
            }
        }
    }

    post {
        always {
            sh "docker rmi ${FULL_IMAGE} || true"
        }
    }
}
