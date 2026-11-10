import SwiftUI

struct ItemDetailView: View {
    let id: UUID
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var isEditing = false
    @State private var draftName = ""
    @State private var draftQuantity = ""
    @State private var isConfirmingDelete = false

    private var deleteButton: some View {
        Button("Delete Item", role: .destructive) {
            isConfirmingDelete = true
        }
        .accessibilityIdentifier("detail.delete")
    }

    var body: some View {
        if let item = store.item(id) {
            Form {
                if isEditing {
                    TextField("Name", text: $draftName)
                        .accessibilityIdentifier("detail.nameField")
                    TextField("Quantity", text: $draftQuantity)
                        .keyboardType(.numberPad)
                        .accessibilityIdentifier("detail.quantityField")
                } else {
                    HStack {
                        Text("Name")
                        Spacer()
                        Text(item.name).accessibilityIdentifier("detail.name")
                    }
                    HStack {
                        Text("Quantity")
                        Spacer()
                        Text("\(item.quantity)").accessibilityIdentifier("detail.quantity")
                    }
                }
                Section {
                    if Mutation.isActive("M04") {
                        // M04 (B4): the Delete button is wrapped in a new container.
                        VStack { deleteButton }
                            .accessibilityElement(children: .contain)
                            .accessibilityIdentifier("detail.dangerZone")
                    } else {
                        deleteButton
                    }
                }
            }
            .navigationTitle(item.name)
            .toolbar {
                if isEditing {
                    Button("Save") {
                        let name = draftName.trimmingCharacters(in: .whitespaces)
                        store.update(
                            id,
                            name: name.isEmpty ? item.name : name,
                            quantity: Int(draftQuantity) ?? item.quantity
                        )
                        isEditing = false
                    }
                    .accessibilityIdentifier("detail.save")
                } else {
                    Button("Edit") {
                        draftName = item.name
                        draftQuantity = "\(item.quantity)"
                        isEditing = true
                    }
                    .accessibilityIdentifier(Mutation.pick("M05", "detail.modify", else: "detail.edit"))
                }
            }
            .alert("Delete \(item.name)?", isPresented: $isConfirmingDelete) {
                Button("Delete", role: .destructive) {
                    store.delete(id)
                    dismiss()
                }
                .accessibilityIdentifier("detail.confirmDelete")
                Button("Cancel", role: .cancel) {}
            }
        }
    }
}
