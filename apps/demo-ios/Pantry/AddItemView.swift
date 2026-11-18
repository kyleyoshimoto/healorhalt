import SwiftUI

struct AddItemView: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var quantity = "1"

    private var trimmedName: String {
        name.trimmingCharacters(in: .whitespaces)
    }

    var body: some View {
        NavigationStack {
            Form {
                TextField("Name", text: $name)
                    .accessibilityIdentifier("add.name")
                TextField("Quantity", text: $quantity)
                    .keyboardType(.numberPad)
                    .accessibilityIdentifier("add.quantity")
            }
            .navigationTitle("Add Item")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .accessibilityIdentifier("add.cancel")
                }
                ToolbarItem(placement: .confirmationAction) {
                    if Mutation.isActive("M19") {
                        // M19 (R2, bug): Save is replaced by a look-alike that closes the form
                        // without adding the item.
                        Button("Save Draft") { dismiss() }
                            .disabled(trimmedName.isEmpty)
                            .accessibilityIdentifier("add.saveDraft")
                    } else {
                        // M23 (R6, bug): reworded "Save" -> "Add" (as in M02) and never enabled.
                        Button(Mutation.pick("M02", "Add", else: Mutation.pick("M23", "Add", else: "Save"))) {
                            store.add(name: trimmedName, quantity: Int(quantity) ?? 1)
                            dismiss()
                        }
                        .disabled(trimmedName.isEmpty || Mutation.isActive("M23"))
                        .accessibilityIdentifier("add.save")
                    }
                }
            }
        }
    }
}
