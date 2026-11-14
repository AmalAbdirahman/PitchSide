// The live worker (python -m live.worker) as one Container App, scaled by KEDA on the
// consumer group's lag on the "events" hub. Also: Log Analytics (container logs, where the
// slice's stdout audit log lands), the Container Apps environment, a registry for the image
// and a managed identity that pulls it. P15 thin slice; written, not deployed.

param prefix string
param location string
param eventHubsNamespace string
param eventHubsRule string
param bootstrap string
param redisCache string
param redisHost string

@description('Worker image in the registry, e.g. <registry>.azurecr.io/pitchside-live:<tag>. Empty: create everything except the Container App (the first pass of infra/deploy.ps1, before the image exists).')
param image string = ''

@description('Replicas when there is no lag. 0 saves cost but the first event waits for a cold start.')
@minValue(0)
param minReplicas int = 0

@description('Upper bound; more replicas than partitions of "events" would sit idle.')
@minValue(1)
param maxReplicas int = 1

var acrPullRole = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var consumerGroup = 'worker' // live/worker.py subscribes to "events" with this group

resource logs 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: replace('${prefix}acr', '-', '')
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: '${prefix}-worker-id'
  location: location
}

resource pull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, identity.id, acrPullRole)
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRole)
  }
}

resource env 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource ehRule 'Microsoft.EventHub/namespaces/authorizationRules@2024-01-01' existing = {
  name: '${eventHubsNamespace}/${eventHubsRule}'
}

resource redisDb 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' existing = {
  name: '${redisCache}/default'
}

// Secrets as Container App secrets for the slice. The design keeps them in Key Vault, read
// through the managed identity (docs/architecture/azure.md, "What is not built").
resource app 'Microsoft.App/containerApps@2025-01-01' = if (!empty(image)) {
  name: '${prefix}-worker'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [pull]
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: registry.properties.loginServer, identity: identity.id }]
      secrets: [
        { name: 'kafka-password', value: ehRule.listKeys().primaryConnectionString }
        {
          name: 'redis-url'
          value: 'rediss://:${redisDb.listKeys().primaryKey}@${redisHost}:10000/0'
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          command: ['python', '-m', 'live.worker']
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'PITCHSIDE_BROKERS', value: bootstrap }
            { name: 'PITCHSIDE_KAFKA_SASL_PASSWORD', secretRef: 'kafka-password' }
            { name: 'PITCHSIDE_REDIS', secretRef: 'redis-url' }
            { name: 'PITCHSIDE_AUDIT', value: 'stdout' }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'events-lag'
            custom: {
              type: 'kafka'
              metadata: {
                bootstrapServers: bootstrap
                consumerGroup: consumerGroup
                topic: 'events'
                lagThreshold: '50'
                offsetResetPolicy: 'earliest'
                sasl: 'plaintext'
                tls: 'enable'
                username: '$ConnectionString'
              }
              auth: [{ secretRef: 'kafka-password', triggerParameter: 'password' }]
            }
          }
        ]
      }
    }
  }
}

output registryName string = registry.name
output loginServer string = registry.properties.loginServer
output workerName string = empty(image) ? '' : app.name
output logsWorkspace string = logs.name
