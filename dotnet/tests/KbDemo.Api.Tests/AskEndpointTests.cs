using System.Net;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Options;

namespace KbDemo.Api.Tests;

public class AskEndpointTests
{
    private static readonly Passage Travel = new("travel.md", 0.7772, "Perth: A$255 a night.");
    private static readonly CancellationToken Cancel = TestContext.Current.CancellationToken;

    /// <summary>The whole service in memory, with the knowledge base and the model faked.</summary>
    private static WebApplicationFactory<Program> App(string knowledgeBaseId = "KB00000001") =>
        new WebApplicationFactory<Program>().WithWebHostBuilder(web =>
        {
            web.UseSetting("KB_DEMO_KNOWLEDGE_BASE_ID", knowledgeBaseId);
            web.UseSetting("KB_DEMO_MODEL_ID", "au.test");
            web.ConfigureTestServices(services =>
            {
                services.AddSingleton<IPassageRetriever>(new FakeRetriever(Travel));
                services.AddSingleton<IChatClient>(new FakeChatClient("Up to A$255 a night [1]."));
            });
        });

    [Fact]
    public async Task The_health_check_answers_without_calling_AWS()
    {
        using var app = App();

        var response = await app.CreateClient().GetAsync("/healthz", Cancel);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        Assert.Equal("Healthy", await response.Content.ReadAsStringAsync(Cancel));
    }

    [Fact]
    public async Task The_answer_has_exactly_the_fields_the_Python_gate_reads()
    {
        using var app = App();

        var response = await app.CreateClient().PostAsJsonAsync("/ask", new { question = "  Hotel limit in Perth? " }, Cancel);

        Assert.Equal(HttpStatusCode.OK, response.StatusCode);
        using var body = JsonDocument.Parse(await response.Content.ReadAsStringAsync(Cancel));
        var answer = body.RootElement;
        Assert.Equal(["question", "passages", "text", "unavailable"], answer.EnumerateObject().Select(field => field.Name));
        var passage = Assert.Single(answer.GetProperty("passages").EnumerateArray());
        Assert.Equal(["source", "score", "text"], passage.EnumerateObject().Select(field => field.Name));
        Assert.Equal("Hotel limit in Perth?", answer.GetProperty("question").GetString());
        Assert.Equal("travel.md", passage.GetProperty("source").GetString());
        Assert.Equal(0.7772, passage.GetProperty("score").GetDouble());
        Assert.Equal("Up to A$255 a night [1].", answer.GetProperty("text").GetString());
        Assert.Equal(JsonValueKind.Null, answer.GetProperty("unavailable").ValueKind);
    }

    [Theory]
    [InlineData("{}")]
    [InlineData("{\"question\": \"   \"}")]
    [InlineData("{\"question\": null}")]
    public async Task A_missing_or_blank_question_gets_a_400_problem_saying_what_to_send(string json)
    {
        using var app = App();

        var response = await app.CreateClient().PostAsync("/ask", new StringContent(json, Encoding.UTF8, "application/json"), Cancel);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
        using var problem = JsonDocument.Parse(await response.Content.ReadAsStringAsync(Cancel));
        Assert.Equal(AskEndpoint.NoQuestion, problem.RootElement.GetProperty("detail").GetString());
    }

    [Fact]
    public async Task A_body_that_is_not_JSON_gets_a_400_problem()
    {
        using var app = App();

        var response = await app.CreateClient().PostAsync("/ask", new StringContent("question?", Encoding.UTF8, "application/json"), Cancel);

        Assert.Equal(HttpStatusCode.BadRequest, response.StatusCode);
        Assert.Equal("application/problem+json", response.Content.Headers.ContentType?.MediaType);
    }

    [Fact]
    public void Without_a_knowledge_base_id_the_service_refuses_to_start()
    {
        using var app = App(knowledgeBaseId: "");

        var error = Assert.Throws<OptionsValidationException>(() => app.CreateClient());

        Assert.Contains("KnowledgeBaseId", error.Message);
    }
}
