using Microsoft.Extensions.AI;

namespace KbDemo.Api.Tests;

/// <summary>The knowledge base: hands back fixed passages and records each question.</summary>
internal sealed class FakeRetriever(params Passage[] passages) : IPassageRetriever
{
    public List<string> Questions { get; } = [];

    public Task<IReadOnlyList<Passage>> RetrieveAsync(
        string question,
        CancellationToken cancellationToken)
    {
        Questions.Add(question);
        return Task.FromResult<IReadOnlyList<Passage>>(passages);
    }
}

/// <summary>The model: replies with fixed text, or fails as the test says, and records each call.</summary>
internal sealed class FakeChatClient(string reply = "") : IChatClient
{
    public List<(IReadOnlyList<ChatMessage> Messages, ChatOptions? Options)> Calls { get; } = [];

    public Exception? Failure { get; set; }

    public Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default)
    {
        Calls.Add(([.. messages], options));
        return Failure is null
            ? Task.FromResult(new ChatResponse(new ChatMessage(ChatRole.Assistant, reply)))
            : Task.FromException<ChatResponse>(Failure);
    }

    public IAsyncEnumerable<ChatResponseUpdate> GetStreamingResponseAsync(
        IEnumerable<ChatMessage> messages,
        ChatOptions? options = null,
        CancellationToken cancellationToken = default) =>
        throw new NotSupportedException("The service never streams.");

    public object? GetService(Type serviceType, object? serviceKey = null) => null;

    public void Dispose()
    {
    }
}
