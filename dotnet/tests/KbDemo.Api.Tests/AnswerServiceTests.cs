using System.Net;
using Amazon.BedrockRuntime;
using Amazon.Runtime;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Logging.Abstractions;
using Microsoft.Extensions.Options;

namespace KbDemo.Api.Tests;

public class AnswerServiceTests
{
    private static readonly Passage Travel = new("travel.md", 0.7772, "Perth: A$255 a night.");
    private static readonly Passage Expenses = new("expenses.md", 0.3297, "Dinner: A$52.");
    private static readonly CancellationToken Cancel = TestContext.Current.CancellationToken;

    private static AnswerService Service(FakeChatClient chat, params Passage[] passages) => new(
        new FakeRetriever(passages),
        chat,
        Options.Create(new KbDemoOptions { KnowledgeBaseId = "KB00000001", ModelId = "au.test" }),
        NullLogger<AnswerService>.Instance);

    [Fact]
    public async Task The_model_gets_the_system_prompt_then_the_numbered_passages_and_the_question()
    {
        var chat = new FakeChatClient("Up to A$255 a night [1].");

        await Service(chat, Travel, Expenses).AnswerAsync("Hotel limit in Perth?", Cancel);

        var (messages, _) = Assert.Single(chat.Calls);
        Assert.Equal([ChatRole.System, ChatRole.User], messages.Select(message => message.Role));
        Assert.Equal(Prompt.System, messages[0].Text);
        Assert.Equal(
            "<passages>\n"
                + "<passage number=\"1\" source=\"travel.md\">\nPerth: A$255 a night.\n</passage>\n"
                + "<passage number=\"2\" source=\"expenses.md\">\nDinner: A$52.\n</passage>\n"
                + "</passages>\n\nQuestion: Hotel limit in Perth?",
            messages[1].Text);
    }

    [Fact]
    public async Task Every_call_names_the_model_at_temperature_0_with_400_tokens()
    {
        var chat = new FakeChatClient("Up to A$255 a night [1].");

        await Service(chat, Travel).AnswerAsync("Hotel limit in Perth?", Cancel);

        var (_, options) = Assert.Single(chat.Calls);
        Assert.Equal(("au.test", 0f, 400), (options?.ModelId, options?.Temperature, options?.MaxOutputTokens));
    }

    [Fact]
    public async Task The_answer_carries_the_passages_and_the_model_text()
    {
        var answer = await Service(new FakeChatClient("Up to A$255 a night [1]."), Travel, Expenses)
            .AnswerAsync("Hotel limit in Perth?", Cancel);

        Assert.Equal("Hotel limit in Perth?", answer.Question);
        Assert.Equal([Travel, Expenses], answer.Passages);
        Assert.Equal("Up to A$255 a night [1].", answer.Text);
        Assert.Null(answer.Unavailable);
    }

    [Fact]
    public async Task With_no_passages_the_model_is_still_asked_so_it_can_decline()
    {
        var chat = new FakeChatClient(Prompt.NotCovered);

        var answer = await Service(chat).AnswerAsync("Who won the 1987 grand final?", Cancel);

        Assert.Equal("<passages>\n\n</passages>\n\nQuestion: Who won the 1987 grand final?", chat.Calls[0].Messages[1].Text);
        Assert.Equal(Prompt.NotCovered, answer.Text);
    }

    [Fact]
    public async Task A_model_failure_keeps_the_passages_and_says_why_as_code_and_message()
    {
        var chat = new FakeChatClient
        {
            Failure = new AmazonBedrockRuntimeException(
                "You don't have access to the model with the specified model ID.",
                ErrorType.Sender,
                "AccessDeniedException",
                "request-1",
                HttpStatusCode.Forbidden),
        };

        var answer = await Service(chat, Travel).AnswerAsync("Hotel limit in Perth?", Cancel);

        Assert.Equal([Travel], answer.Passages);
        Assert.Null(answer.Text);
        Assert.Equal(
            "AccessDeniedException: You don't have access to the model with the specified model ID.",
            answer.Unavailable);
    }

    [Fact]
    public async Task A_cancelled_request_is_not_mistaken_for_a_model_failure()
    {
        var chat = new FakeChatClient { Failure = new OperationCanceledException() };

        await Assert.ThrowsAsync<OperationCanceledException>(
            () => Service(chat, Travel).AnswerAsync("Hotel limit in Perth?", Cancel));
    }

    [Fact]
    public void The_system_prompt_is_the_Python_prompt_with_LF_line_endings()
    {
        Assert.DoesNotContain('\r', Prompt.System);
        Assert.Equal(4, Prompt.System.Split('\n').Length);
        Assert.EndsWith(
            "If the passages don't answer the question, reply with exactly: The handbook doesn't cover that.\n"
                + "Answer briefly, in Australian English.",
            Prompt.System);
    }
}
