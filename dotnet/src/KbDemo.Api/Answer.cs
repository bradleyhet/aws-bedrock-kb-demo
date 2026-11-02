namespace KbDemo.Api;

// The JSON the Python release gate reads with Answer.from_json, whose Passage(**passage) rejects
// unknown keys: the names must stay question, passages[source, score, text], text and unavailable.

/// <summary>One retrieved passage: the corpus file it came from, its score and its text.</summary>
internal sealed record Passage(string Source, double Score, string Text);

/// <summary>The passages, and the model's answer or why there isn't one.</summary>
internal sealed record Answer(
    string Question,
    IReadOnlyList<Passage> Passages,
    string? Text = null,
    string? Unavailable = null);

internal sealed record AskRequest(string? Question);
