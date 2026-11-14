// Azure Managed Redis for the worker's match state (RedisState in live/adapters.py).
// Smallest Balanced size, TLS only. P15 thin slice; written, not deployed.

@description('Cache name, globally unique.')
param name string
param location string

@description('Managed Redis size. Balanced_B0 is the smallest; enough for one match.')
param sku string = 'Balanced_B0'

resource cache 'Microsoft.Cache/redisEnterprise@2025-07-01' = {
  name: name
  location: location
  sku: { name: sku }
  properties: { minimumTlsVersion: '1.2', publicNetworkAccess: 'Enabled' } // private endpoints: design only
}

// EnterpriseCluster shows one endpoint to the client, so redis-py's plain client (and the
// worker's MULTI pipeline) works unchanged. Access keys stay on for the slice; the design uses
// Microsoft Entra ID with the worker's managed identity instead.
resource db 'Microsoft.Cache/redisEnterprise/databases@2025-07-01' = {
  parent: cache
  name: 'default'
  properties: {
    clientProtocol: 'Encrypted'
    port: 10000
    clusteringPolicy: 'EnterpriseCluster'
    evictionPolicy: 'NoEviction'
    accessKeysAuthentication: 'Enabled'
  }
}

output cacheName string = cache.name
output hostName string = cache.properties.hostName
