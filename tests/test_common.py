from chatcli.ui.common import pick


def test_pick_returns_the_zero_based_index_of_the_chosen_item(out, prompts):
    prompts("2")
    assert pick("Choose", ["a", "b", "c"]) == 1


def test_pick_zero_means_back(out, prompts):
    prompts("0")
    assert pick("Choose", ["a"]) is None


def test_pick_with_nothing_to_choose_from_asks_nothing(out, prompts):
    prompts()   # any prompt would fail the test
    assert pick("Choose", []) is None
    assert out.getvalue() == ""


def test_pick_asks_again_until_the_answer_is_a_valid_number(out, prompts):
    prompts("x", "-1", "4", "1.5", "", "3")
    assert pick("Choose", ["a", "b", "c"]) == 2
    assert out.getvalue().count("Enter a number from the list.") == 5


def test_labels_keep_their_bracketed_text(out, prompts):
    # Regression: "[gpt-4o]" was read as a style tag, so pickers never showed the model.
    prompts("0")
    pick("Choose", ["ep  [gpt-4o]  http://x"])
    assert "1. ep [gpt-4o] http://x" in " ".join(out.getvalue().split())


def test_a_label_with_a_stray_closing_tag_does_not_crash(out, prompts):
    prompts("0")
    pick("Choose", ["name [/oops]"])
    assert "name [/oops]" in out.getvalue()
