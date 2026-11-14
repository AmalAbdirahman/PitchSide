// P15 thin slice of docs/architecture/azure.md: Event Hubs (Kafka endpoint), Azure Managed Redis
// and the live worker as one Container App. Written and built, NOT deployed. Deploy only with
// infra/deploy.ps1 -Deploy, after reading the cost section of the design note.
// Not in the slice: the API, Web PubSub, Postgres, Blob Storage, Key Vault, Foundry, App Insights.

targetScope = 'resourceGroup'

@description('Short lowercase prefix for every resource name; add a suffix to make names unique.')
@minLength(3)
@maxLength(16)
param prefix string = 'pitchside${take(uniqueString(resourceGroup().id), 6)}'

param location string = resourceGroup().location

@description('Worker image; empty on the first pass, before the image is built into the registry.')
param image string = ''

@minValue(0)
param minReplicas int = 0

module eventHubs 'modules/eventhubs.bicep' = {
  name: 'eventhubs'
  params: { name: '${prefix}-eh', location: location }
}

module redis 'modules/redis.bicep' = {
  name: 'redis'
  params: { name: '${prefix}-redis', location: location }
}

module worker 'modules/worker.bicep' = {
  name: 'worker'
  params: {
    prefix: prefix
    location: location
    eventHubsNamespace: eventHubs.outputs.namespaceName
    eventHubsRule: eventHubs.outputs.ruleName
    bootstrap: eventHubs.outputs.bootstrap
    redisCache: redis.outputs.cacheName
    redisHost: redis.outputs.hostName
    image: image
    minReplicas: minReplicas
  }
}

output registryName string = worker.outputs.registryName
output loginServer string = worker.outputs.loginServer
output workerName string = worker.outputs.workerName
output eventHubsNamespace string = eventHubs.outputs.namespaceName
output bootstrap string = eventHubs.outputs.bootstrap
output logsWorkspace string = worker.outputs.logsWorkspace
