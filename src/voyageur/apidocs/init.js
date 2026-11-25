// The API page: Swagger UI over this service's own /openapi.json, with "Try it out" on so every example can be sent as is.
window.ui = SwaggerUIBundle({
  url: "/openapi.json",
  dom_id: "#ui",
  deepLinking: true,
  tryItOutEnabled: true,
  persistAuthorization: false,
  displayRequestDuration: true,
  defaultModelsExpandDepth: 0,
  docExpansion: "list",
});
