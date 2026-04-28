from server.tools import list_documents, read_document


def main():
    print("🧠 Agent starting MCP-style workflow\n")

    # Step 1: discover tools
    print("🔧 Calling tool: list_documents")
    documents = list_documents()
    print("✅ Response:", documents, "\n")

    # Step 2: select a document
    selected = documents[0]
    print(f"🧠 Agent selected document: {selected}\n")

    # Step 3: read document
    print(f"🔧 Calling tool: read_document ({selected})")
    content = read_document(selected)
    print("✅ Response:\n", content)

    print("\n✅ Agent workflow complete")


if __name__ == "__main__":
    main()