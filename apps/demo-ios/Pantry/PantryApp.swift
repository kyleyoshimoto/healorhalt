import SwiftUI

@main
struct PantryApp: App {
    @State private var store = Store()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(store)
        }
    }
}

struct RootView: View {
    @Environment(Store.self) private var store

    var body: some View {
        if store.isLoggedIn {
            ItemListView()
        } else {
            LoginView()
        }
    }
}
