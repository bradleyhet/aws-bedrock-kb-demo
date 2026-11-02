using Amazon;
using Amazon.BedrockAgentRuntime;
using Amazon.BedrockRuntime;
using KbDemo.Api;
using Microsoft.AspNetCore.RateLimiting;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Options;

var builder = WebApplication.CreateBuilder(args);

// One JSON object per log line, so CloudWatch Logs Insights can query by field.
builder.Logging.ClearProviders();
builder.Logging.AddJsonConsole();

builder.Services.AddOptions<KbDemoOptions>()
    .Bind(builder.Configuration)
    .ValidateDataAnnotations()
    .ValidateOnStart();

// Every AWS client gets its region explicitly, as every boto3 client does on the Python side.
builder.Services.AddSingleton<IAmazonBedrockAgentRuntime>(services =>
    new AmazonBedrockAgentRuntimeClient(Region(services)));
builder.Services.AddSingleton<IPassageRetriever, BedrockPassageRetriever>();
builder.Services.AddChatClient(services =>
        new AmazonBedrockRuntimeClient(Region(services)).AsIChatClient())
    .UseLogging();
builder.Services.AddSingleton<AnswerService>();

builder.Services.AddProblemDetails();
// In Development, minimal APIs throw on a body that isn't JSON, and the exception handler then
// answers 500. Answer 400 in every environment instead.
builder.Services.Configure<RouteHandlerOptions>(routes => routes.ThrowOnBadRequest = false);
builder.Services.AddHealthChecks();
builder.Services.AddRateLimiter(limiter =>
{
    limiter.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    limiter.AddFixedWindowLimiter(AskEndpoint.RateLimit, window =>
    {
        window.PermitLimit = 10;
        window.Window = TimeSpan.FromSeconds(10);
    });
});

var app = builder.Build();

app.UseExceptionHandler();
app.UseStatusCodePages();
app.UseRateLimiter();

app.MapHealthChecks("/healthz");
app.MapPost("/ask", AskEndpoint.HandleAsync).RequireRateLimiting(AskEndpoint.RateLimit);

app.Run();

static RegionEndpoint Region(IServiceProvider services) =>
    RegionEndpoint.GetBySystemName(services.GetRequiredService<IOptions<KbDemoOptions>>().Value.Region);
